"""Which Lightroom photos are wanted, and which folder each goes to. No Home Assistant imports."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from .album_layout import sanitize_name
from .lightroom_catalog import Album, Photo


FLAGS = ('any', 'not_rejected', 'picks_only')
ORIENTATIONS = ('any', 'landscape', 'portrait')


@dataclass(frozen=True)
class Filter:
    ratings: frozenset[int] = frozenset({3, 4, 5})
    flag: str = 'not_rejected'  # 'any', 'not_rejected' or 'picks_only'
    albums_include: frozenset[str] = frozenset()
    albums_exclude: frozenset[str] = frozenset()
    include_unfiled: bool = True
    captured_from: date | None = None
    captured_to: date | None = None
    captured_last_years: int | None = None
    edited_only: bool = False
    equipment_include: tuple[str, ...] = ()
    equipment_exclude: tuple[str, ...] = ()
    orientation: str = 'any'  # or 'landscape', 'portrait'
    keywords_include: tuple[str, ...] = ()
    keywords_exclude: tuple[str, ...] = ()


@dataclass(frozen=True)
class Placed:
    photo: Photo
    key: str  # album id, or 'YYYY-MM' for a photo in no album
    folder: str  # relative to photo_dir, prefix included


def filter_from_options(options: dict) -> Filter:
    o = options or {}
    d = Filter()

    def day(key):
        v = o.get(key)
        return date.fromisoformat(v) if v else None

    def texts(key):
        return tuple(str(x) for x in o.get(key) or ())

    return Filter(
        ratings=frozenset(int(x) for x in o['ratings']) if 'ratings' in o else d.ratings,
        flag=o['flag'] if o.get('flag') in FLAGS else d.flag,
        albums_include=frozenset(o.get('albums_include') or ()),
        albums_exclude=frozenset(o.get('albums_exclude') or ()),
        include_unfiled=bool(o.get('include_unfiled', d.include_unfiled)),
        captured_from=day('captured_from'), captured_to=day('captured_to'),
        captured_last_years=o.get('captured_last_years'),
        edited_only=bool(o.get('edited_only', d.edited_only)),
        equipment_include=texts('equipment_include'), equipment_exclude=texts('equipment_exclude'),
        orientation=o['orientation'] if o.get('orientation') in ORIENTATIONS else d.orientation,
        keywords_include=texts('keywords_include'), keywords_exclude=texts('keywords_exclude'))


def filter_to_options(f: Filter) -> dict:
    return {
        'ratings': sorted(f.ratings), 'flag': f.flag,
        'albums_include': sorted(f.albums_include), 'albums_exclude': sorted(f.albums_exclude),
        'include_unfiled': f.include_unfiled,
        'captured_from': f.captured_from.isoformat() if f.captured_from else None,
        'captured_to': f.captured_to.isoformat() if f.captured_to else None,
        'captured_last_years': f.captured_last_years, 'edited_only': f.edited_only,
        'equipment_include': list(f.equipment_include), 'equipment_exclude': list(f.equipment_exclude),
        'orientation': f.orientation,
        'keywords_include': list(f.keywords_include), 'keywords_exclude': list(f.keywords_exclude),
    }


def folder_name(name: str) -> str:
    return sanitize_name(name).lstrip('@') or 'album'


def album_path(album_id: str, albums: dict[str, Album]) -> str:
    """The album's path in the album tree: its parent sets' safe names, outermost first, then its own.

    A parent missing from `albums`, or a loop in the parents, ends the walk there.
    """
    parts, seen, cur = [], set(), album_id
    while cur in albums and cur not in seen:
        seen.add(cur)
        parts.append(folder_name(albums[cur].name))
        cur = albums[cur].parent_id
    return '/'.join(reversed(parts))


def _album_folders(albums: dict[str, Album]) -> dict[str, str]:
    """Album id -> tree path; an album whose path is a set's takes the next free suffix, so an
    album folder never holds another album's folder."""
    set_paths = {album_path(i, albums) for i, a in albums.items() if a.is_set}
    out = {}
    for i, a in albums.items():
        if a.is_set:
            continue
        path = candidate = album_path(i, albums)
        n = 1
        while candidate in set_paths:
            n += 1
            candidate = f'{path}-{n}'
        out[i] = candidate
    return out


def _years_back(today: date, years: int) -> date:
    try:
        return today.replace(year=today.year - years)
    except ValueError:  # 29 Feb in a non-leap year
        return today.replace(year=today.year - years, day=28)


def _expand(ids: frozenset[str], albums: dict[str, Album]) -> set[str]:
    """The ids that exist, and every album below a set among them."""
    out = {i for i in ids if i in albums}
    children: dict[str | None, list[str]] = {}
    for a in albums.values():
        children.setdefault(a.parent_id, []).append(a.id)
    stack = list(out)
    while stack:
        for c in children.get(stack.pop(), ()):
            if c not in out:
                out.add(c)
                stack.append(c)
    return out


def _equipment_hit(photo: Photo, terms: tuple[str, ...]) -> bool:
    fields = [v.casefold() for v in (photo.meta.make, photo.meta.model, photo.meta.lens) if v]
    return any(t.casefold() in f for t in terms for f in fields)


def _keyword_hit(photo: Photo, terms: tuple[str, ...]) -> bool:
    have = {k.casefold() for k in photo.keywords}
    return any(t.casefold() in have for t in terms)


def _wanted(p: Photo, f: Filter, start: date | None, end: date | None) -> bool:
    if p.rating not in f.ratings:
        return False
    if f.flag == 'picks_only' and p.flag != 'pick':
        return False
    if f.flag == 'not_rejected' and p.flag == 'reject':
        return False
    day = p.captured.date()
    if (start and day < start) or (end and day > end):
        return False
    if f.edited_only and not p.edited:
        return False
    if f.equipment_include and not _equipment_hit(p, f.equipment_include):
        return False
    if f.equipment_exclude and _equipment_hit(p, f.equipment_exclude):
        return False
    if f.orientation in ('landscape', 'portrait'):
        if p.width is None or p.height is None:
            return False
        if f.orientation == 'landscape' and not p.width > p.height:
            return False
        if f.orientation == 'portrait' and not p.height > p.width:
            return False
    if f.keywords_include and not _keyword_hit(p, f.keywords_include):
        return False
    return not (f.keywords_exclude and _keyword_hit(p, f.keywords_exclude))


def place(photos: Iterable[Photo], f: Filter, albums: dict[str, Album], prefix: str,
          today: date) -> list[Placed]:
    start, end = f.captured_from, f.captured_to
    if f.captured_last_years is not None:
        start, end = _years_back(today, f.captured_last_years), None
    include = _expand(f.albums_include, albums)
    exclude = _expand(f.albums_exclude, albums)
    collections = {i for i, a in albums.items() if not a.is_set}
    allowed = ((include & collections) if include else collections) - exclude
    paths = _album_folders(albums)

    def folder(name: str) -> str:
        return f'{prefix}/{name}' if prefix else name

    out = []
    for p in photos:
        if not _wanted(p, f, start, end):
            continue
        if not p.album_ids:
            if f.include_unfiled:
                key = p.captured.strftime('%Y-%m')
                out.append(Placed(p, key, folder(key)))
            continue
        eligible = [albums[i] for i in p.album_ids if i in allowed]
        if not eligible:
            continue
        a = min(eligible, key=lambda x: (x.name.casefold(), x.id))
        out.append(Placed(p, a.id, folder(paths[a.id])))
    return out
