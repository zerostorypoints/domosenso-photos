"""Plan and apply Lightroom changes on the folders this source owns (no Home Assistant import)."""
from __future__ import annotations

import errno
import hashlib
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import tzinfo
from pathlib import Path, PurePosixPath
from typing import Any

from .album_files import MARKER, free_bytes, read_marker, remove_photo, write_marker, write_photo
from .album_layout import PhotoDirMissing, inside, make_dirs, prune_empty, rename_target, resolve_path
from .lightroom_filter import Placed

MASS_MIN = 20
_MONTH = re.compile(r'\d{4}-\d{2}')
RunBlocking = Callable[[Callable[[], Any]], Awaitable[Any]]


class DiskFull(Exception):
    pass


def owner_for(account: str) -> str:
    """The marker owner of an Adobe account: a short hash, since markers are served without login."""
    return 'lightroom:' + hashlib.sha256(account.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class OwnedFolder:
    key: str
    path: str
    items: dict[str, dict]  # asset id -> {'name': str, 'changed': str}


@dataclass(frozen=True)
class LrPlan:
    add: list[Placed]
    update: list[Placed]
    move: list[tuple[Placed, str]]  # (placed, old key)
    remove: list[tuple[str, str]]  # (key, asset id)
    rename: list[tuple[str, str, str]]  # (key, old path, new wanted path)
    hold: str | None
    # The folders the plan was made against (one per key), so execute finds a key's path.
    owned: dict[str, OwnedFolder] = field(default_factory=dict)
    # Keys with more than one candidate folder (key, folder paths): nothing under them is touched.
    ambiguous: list[tuple[str, list[str]]] = field(default_factory=list)


@dataclass
class RunResult:
    added: int = 0
    updated: int = 0
    moved: int = 0
    removed: int = 0
    skipped: list[str] = field(default_factory=list)


def file_name(p: Placed) -> str:
    return f'{p.photo.captured:%Y-%m-%d}_{p.photo.asset_id}.jpg'


def _plain(name) -> bool:
    """A bare file name from a marker: no separator, not empty, not `.` or `..`."""
    return isinstance(name, str) and name not in ('', '.', '..') and PurePosixPath(name).name == name


def _ours(marker: dict | None, owner: str, key: str | None = None) -> bool:
    return (isinstance(marker, dict) and marker.get('owner') == owner and isinstance(marker.get('key'), str)
            and (key is None or marker['key'] == key) and isinstance(marker.get('items'), dict))


def _present(folder: Path, items: dict) -> dict[str, dict]:
    """Marker entries with a plain name whose file is still there; a missing file is fetched again."""
    return {str(k): dict(v) for k, v in items.items()
            if isinstance(v, dict) and _plain(v.get('name')) and (folder / v['name']).is_file()}


class Owned(dict):
    """read_owned's result: key -> the one folder managed for it, plus the keys left alone this run."""

    def __init__(self, folders=(), ambiguous: list[tuple[str, list[str]]] | None = None):
        super().__init__(folders)
        self.ambiguous = ambiguous or []  # (key, folder paths)


def _hidden(name: str) -> bool:
    return name.startswith(('.', '@'))  # hidden folders, and a NAS's @eaDir thumbnails


def _folders_below(base: Path):
    """Every folder below base, sorted, depth first; hidden and @ folders and links are skipped. A
    folder holding a record is not descended into: an album folder never holds another album."""
    try:
        children = sorted((c for c in base.iterdir() if c.is_dir() and not c.is_symlink() and not _hidden(c.name)),
                          key=lambda c: c.name)
    except OSError:
        return
    for child in children:
        yield child
        if not (child / MARKER).exists():
            yield from _folders_below(child)


def read_owned(photo_dir: Path, prefix: str, owner: str) -> Owned:
    """The folders this owner manages, one per key; no deletion ever depends on guessing a copy.

    A folder is self-recorded when its marker records the path it is at (or records none). Per key:
    exactly one self-recorded folder is managed and every other folder of the key is left out (copies);
    no self-recorded folder and a single candidate is the original moved by the user, adopted (the next
    save records its path); anything else makes the key ambiguous: none of its folders is touched.
    """
    base = photo_dir / prefix if prefix else photo_dir
    groups: dict[str, list[tuple[OwnedFolder, bool]]] = {}
    if not base.is_dir():
        return Owned()
    for child in _folders_below(base):
        rel = child.relative_to(base).as_posix()
        path = f'{prefix}/{rel}' if prefix else rel
        if not inside(photo_dir, path):
            continue
        marker = read_marker(child)
        if not _ours(marker, owner):
            continue
        recorded = marker.get('path')
        folder = OwnedFolder(marker['key'], path, _present(child, marker['items']))
        groups.setdefault(folder.key, []).append((folder, not isinstance(recorded, str) or recorded == path))
    out, ambiguous = {}, []
    for key, group in groups.items():
        selves = [f for f, self_recorded in group if self_recorded]
        if len(selves) == 1:
            out[key] = selves[0]
        elif not selves and len(group) == 1:
            out[key] = group[0][0]
        else:
            ambiguous.append((key, [f.path for f, _ in group]))
    return Owned(out, ambiguous)


def plan(placed: list[Placed], owned: Owned, listed_assets: int, confirm: str | None) -> LrPlan:
    """confirm is the hold the user confirmed: it releases that hold only, never another one."""
    if not isinstance(owned, Owned):
        raise TypeError('plan needs read_owned\'s result')
    ambiguous = list(owned.ambiguous)
    left_alone = {key for key, _ in ambiguous}
    wanted: dict[str, Placed] = {}
    for p in placed:
        wanted.setdefault(p.photo.asset_id, p)
    # A photo placed under an ambiguous key is neither written nor removed from where it is now.
    held = {asset for asset, p in wanted.items() if p.key in left_alone}
    wanted = {asset: p for asset, p in wanted.items() if asset not in held}
    folder_for = {p.key: p.folder for p in wanted.values()}
    kept = folders = {f.key: f for f in owned.values() if f.key not in left_alone}  # one per key

    where: dict[str, list[str]] = {}  # asset id -> keys of kept folders holding it
    for key in sorted(kept):
        for asset in kept[key].items:
            where.setdefault(asset, []).append(key)
    total = len(where)

    add, update, move, remove, stale = [], [], [], [], []
    for asset, p in wanted.items():
        holders = where.get(asset, [])
        if p.key in holders:
            if kept[p.key].items[asset].get('changed') != p.photo.changed:
                update.append(p)
            stale += [(k, asset) for k in holders if k != p.key]
        elif holders:
            move.append((p, holders[0]))
            stale += [(k, asset) for k in holders[1:]]
        else:
            add.append(p)
    gone = [asset for asset in where if asset not in wanted and asset not in held]
    for asset in gone:
        remove.append((where[asset][0], asset))
        stale += [(k, asset) for k in where[asset][1:]]

    # A folder off its placed path is offered for a rename; execute keeps it where it is when the
    # placed path is taken and its current name is the first free suffix (a stable collision suffix).
    rename = [(key, f.path, folder_for[key]) for key, f in sorted(kept.items())
              if key in folder_for and f.path != folder_for[key]]

    unfiled = sum(1 for p, old in move if _MONTH.fullmatch(p.key) and not _MONTH.fullmatch(folders[old].key))
    hold = None
    if listed_assets == 0 and total > 0:
        hold = 'empty_library'
    elif total >= MASS_MIN and len(remove) > total / 2:
        hold = 'mass_remove'
    elif total >= MASS_MIN and unfiled > total / 2:
        hold = 'mass_unfile'
    if hold and hold == confirm:
        hold = None
    if hold:  # nothing that changes what is already there, renames included
        move, remove, stale, rename = [], [], [], []
    return LrPlan(add, update, move, remove + stale, rename, hold, folders, ambiguous)


PREVIEW_TOP = 5


def preview(placed: list[Placed], owned: Owned, listed_assets: int | None = None) -> dict:
    """What a sync with these placements would do, before any hold, and the hold it would raise.

    The counts are the changes once confirmed, so a held filter change still shows its size. A move
    counts as an add to its new folder and a removal from its old one in `top`. listed_assets is the
    library's asset count when known (an empty library holds); None leaves that check out.
    """
    listed = 1 if listed_assets is None else listed_assets
    held = plan(placed, owned, listed, None).hold
    full = plan(placed, owned, listed, held)
    paths = {key: f.path for key, f in full.owned.items()}
    wanted = {p.photo.asset_id for p in placed}
    removed = {(key, asset) for key, asset in full.remove if asset not in wanted}
    moved = {p.photo.asset_id for p, _ in full.move}
    changed: dict[str, list[int]] = {}

    def bump(folder: str, i: int) -> None:
        changed.setdefault(folder, [0, 0])[i] += 1

    for p in full.add:
        bump(paths.get(p.key, p.folder), 0)
    for p, old in full.move:
        bump(paths.get(p.key, p.folder), 0)
        bump(paths[old], 1)
    for key, _ in removed:
        bump(paths[key], 1)
    in_place = {asset for f in full.owned.values() for asset in f.items} & wanted
    top = sorted(changed.items(), key=lambda kv: (-sum(kv[1]), kv[0]))[:PREVIEW_TOP]
    return {'kept': len(in_place - moved), 'add': len(full.add), 'remove': len({a for _, a in removed}),
            'move': len(full.move), 'hold': held,
            'top': [{'folder': folder, 'add': a, 'remove': r} for folder, (a, r) in top]}


def collisions(placed: list[Placed], owned: dict[str, OwnedFolder]) -> list[str]:
    """Owned folders at `<wanted>-<n>` because another folder held the wanted name."""
    wanted = {p.key: p.folder for p in placed}
    return sorted(f.path for f in owned.values()
                  if f.key in wanted and re.fullmatch(re.escape(wanted[f.key]) + r'-\d+', f.path))


@dataclass
class _Exec:
    plan: LrPlan
    photo_dir: Path
    owner: str
    run_blocking: RunBlocking
    now: int
    paths: dict[str, str] = field(default_factory=dict)  # plan dict key -> current path
    prefix: str = ''  # emptied folders are pruned up to here, never above
    moving: set[str] = field(default_factory=set)  # paths of folders this sync moves
    markers: dict[str, dict | None] = field(default_factory=dict)  # plan dict key -> marker read from disk

    async def io(self, fn, *args):
        return await self.run_blocking(lambda: fn(*args))

    def load(self, dkey: str) -> dict | None:
        """The folder's marker, only while the folder is inside photo_dir and still ours for its key."""
        if dkey not in self.markers:
            path, f = self.paths.get(dkey), self.plan.owned.get(dkey)
            marker = None
            if path and f and inside(self.photo_dir, path):
                found = read_marker(self.photo_dir / path)
                if _ours(found, self.owner, f.key):
                    marker = self._fresh(f.key, _present(self.photo_dir / path, found['items']))
            self.markers[dkey] = marker
        return self.markers[dkey]

    def _fresh(self, key: str, items: dict) -> dict:
        return {'owner': self.owner, 'kind': 'lightroom', 'key': key, 'items': items, 'last_sync': self.now}

    def _write_marker(self, dkey: str) -> None:
        marker = self.markers[dkey]
        marker['path'], marker['last_sync'] = self.paths[dkey], self.now
        write_marker(self.photo_dir / self.paths[dkey], marker)

    def _holds_record(self, path: str) -> bool:
        """An album folder (any owner's, any source's record), or a file: neither can be a parent."""
        return (self.photo_dir / path / MARKER).exists() or (self.photo_dir / path).is_file()

    def blocker(self, path: str, skip: str | None = None) -> str | None:
        """The first folder above path that holds a record (an album folder: never a parent)."""
        parts = PurePosixPath(path).parts
        for i in range(1, len(parts)):
            above = '/'.join(parts[:i])
            if above != skip and self._holds_record(above):
                return above
        return None

    def unblocked(self, path: str, skip: str | None = None) -> str:
        """path with every folder above it that holds a record replaced by its first free suffix
        (`2023-05` -> `2023-05-2`), so an album folder never lands inside another one."""
        parts = list(PurePosixPath(path).parts)
        for i in range(len(parts) - 1):
            above = '/'.join(parts[:i + 1])
            if above == skip or not self._holds_record(above):
                continue
            base, n = parts[i], 1
            while True:
                n += 1
                parts[i] = f'{base}-{n}'
                if not self._holds_record('/'.join(parts[:i + 1])):
                    break
        return '/'.join(parts)

    def rename(self, dkey: str, old: str, wanted: str, final: bool = False) -> bool:
        """Move the folder to its wanted path; False when a folder that moves in this sync is in
        the way (try again once the others moved). A folder in the way that stays gives its name a
        suffix in the wanted path."""
        if self.paths.get(dkey) != old or not inside(self.photo_dir, wanted) or self.load(dkey) is None:
            return True
        if any(c.is_dir() and not _hidden(c.name) for c in (self.photo_dir / old).iterdir()):
            return True  # a subfolder is not ours (another source's folder, say): never carried along
        own = old if wanted.startswith(old + '/') else None  # an album moving into a set of its own name
        in_the_way = self.blocker(wanted, skip=own)
        if in_the_way is not None:
            if not final and in_the_way in self.moving:
                return False
            wanted = self.unblocked(wanted, skip=own)
            if not inside(self.photo_dir, wanted):
                return True
        new = rename_target(self.photo_dir, old, wanted)
        if new == old:
            return True
        source = self.photo_dir / old
        if own is not None:  # into a folder at its own path: step aside under a hidden name first
            source = source.rename(source.with_name(f'.{source.name}.moving'))
        make_dirs(self.photo_dir, str(PurePosixPath(new).parent))
        source.rename(self.photo_dir / new)
        self.paths[dkey] = new
        self._write_marker(dkey)  # the marker records where the folder now is
        prune_empty(self.photo_dir, self.prefix, old)
        return True

    def target(self, p: Placed) -> tuple[str, dict] | None:
        """The folder a placed photo is written to, created when this key has none; None if unsafe."""
        if self.load(p.key) is not None:
            return self.paths[p.key], self.markers[p.key]
        wanted = self.unblocked(p.folder)
        if not inside(self.photo_dir, wanted):
            return None
        path = resolve_path(self.photo_dir, wanted, self.owner, p.key)
        if not inside(self.photo_dir, path):
            return None
        folder = self.photo_dir / path
        if _ours(read_marker(folder), self.owner, p.key):
            return None  # a folder of this key the plan did not choose (a copy): never written into
        make_dirs(self.photo_dir, path)
        self.paths[p.key], self.markers[p.key] = path, self._fresh(p.key, {})
        self._write_marker(p.key)  # mark the folder ours before the first file lands
        return path, self.markers[p.key]

    def save(self, dkey: str) -> None:
        """Write the folder's marker; a folder left with nothing but its marker loses both."""
        folder, marker = self.photo_dir / self.paths[dkey], self.markers[dkey]
        self._write_marker(dkey)
        if not marker['items'] and [c.name for c in folder.iterdir()] == [MARKER]:
            (folder / MARKER).unlink()
            folder.rmdir()
            prune_empty(self.photo_dir, self.prefix, self.paths[dkey])
            self.markers[dkey] = None
            del self.paths[dkey]

    def drop(self, dkey: str, asset: str) -> bool:
        """Remove one owned file named by the folder's marker; False when there is nothing ours to remove."""
        marker = self.load(dkey)
        entry = marker['items'].get(asset) if marker else None
        if entry is None:
            return False
        remove_photo(self.photo_dir / self.paths[dkey], entry['name'])
        del marker['items'][asset]
        self.save(dkey)
        return True

    def write(self, p: Placed, data: bytes, tz: tzinfo) -> bool:
        spot = self.target(p)
        if spot is None:
            return False
        path, marker = spot
        name, asset = file_name(p), p.photo.asset_id
        previous = marker['items'].get(asset)
        write_photo(self.photo_dir / path, name, data, int(p.photo.captured.replace(tzinfo=tz).timestamp()))
        marker['items'][asset] = {'name': name, 'changed': p.photo.changed}
        self.save(p.key)
        if previous and previous['name'] != name:  # the capture date changed, so did the name
            remove_photo(self.photo_dir / path, previous['name'])
        return True


async def execute(plan: LrPlan, photo_dir: Path, owner: str, fetch: Callable[[Placed], Awaitable[bytes | None]],
                  run_blocking: RunBlocking, free_floor: int, tz: tzinfo, now: int,
                  progress: Callable[[str, int, int], None] | None = None, prefix: str = '') -> RunResult:
    """Apply the plan; raises PhotoDirMissing (nothing touched) or DiskFull (stops before the next write)."""
    if not await run_blocking(photo_dir.is_dir):  # unmounted storage: touch nothing, never re-create it
        raise PhotoDirMissing
    run = _Exec(plan, photo_dir, owner, run_blocking, now, {k: f.path for k, f in plan.owned.items()}, prefix=prefix)
    result = RunResult()
    try:
        run.moving = {old for _, old, _ in plan.rename}
        waiting = [r for r in plan.rename if not await run.io(run.rename, *r)]
        for r in waiting:  # the folder in the way has moved out by now, or it stays: then a suffix
            await run.io(run.rename, *r, True)
        writes = ([(p, None, 'added') for p in plan.add] + [(p, None, 'updated') for p in plan.update]
                  + [(p, old, 'moved') for p, old in plan.move])
        total = len(writes) + len(plan.remove)
        report = progress or (lambda phase, done, total: None)
        if writes:
            report('downloading', 0, total)
        for i, (p, old, counter) in enumerate(writes, 1):
            if await run.io(free_bytes, photo_dir) < free_floor:
                raise DiskFull
            data = await fetch(p)
            if data is None or not await run.io(run.write, p, data, tz):
                result.skipped.append(p.photo.asset_id)
            else:
                if old is not None:  # the new copy and its marker are on disk; now the old one goes
                    await run.io(run.drop, old, p.photo.asset_id)
                setattr(result, counter, getattr(result, counter) + 1)
            report('downloading', i, total)
        if plan.remove:
            report('removing', len(writes), total)
        for i, (dkey, asset) in enumerate(plan.remove, len(writes) + 1):
            if await run.io(run.drop, dkey, asset):
                result.removed += 1
            report('removing', i, total)
    except OSError as err:
        if err.errno == errno.ENOSPC:
            raise DiskFull from err
        raise
    return result
