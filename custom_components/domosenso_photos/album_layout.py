"""Folder layout and file names for album sync."""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, tzinfo
from pathlib import Path, PurePosixPath

from .album_files import read_marker
from .album_share import RemoteItem

_BAD = re.compile(r'[/\\\x00-\x1f\x7f]')
MAX_NAME = 80
MAX_BYTES = 200


def sanitize_name(name: str) -> str:
    text = _BAD.sub('_', unicodedata.normalize('NFC', name)).lstrip(' .')[:MAX_NAME]
    text = text.encode('utf-8')[:MAX_BYTES].decode('utf-8', 'ignore')
    return text.rstrip(' ') or 'album'


def initial_path(name: str, start_time: int, tz: tzinfo) -> str:
    return f'{datetime.fromtimestamp(start_time, tz):%Y/%m}/{sanitize_name(name)}'


class PhotoDirMissing(Exception):
    pass


def _owner(folder: Path) -> tuple[str | None, str | None]:
    """(owner, key) of a folder's marker; a Synology marker names its owner `album_id` and has no key."""
    marker = read_marker(folder) or {}
    value = marker.get('owner', marker.get('album_id'))
    key = marker.get('key')
    return (value if isinstance(value, str) else None), (key if isinstance(key, str) else None)


def resolve_path(photo_dir: Path, wanted: str, own_id: str, key: str | None = None) -> str:
    candidate, n = wanted, 1
    while (photo_dir / candidate).exists() and _owner(photo_dir / candidate) != (own_id, key):
        n += 1
        candidate = f'{wanted}-{n}'
    return candidate


def inside(photo_dir: Path, path: str) -> bool:
    root = photo_dir.resolve()
    target = (photo_dir / path).resolve()
    return target != root and target.is_relative_to(root)


def rename_target(photo_dir: Path, path: str, wanted: str) -> str:
    """First of wanted, wanted-2, ... that is free; the current path if it comes first."""
    candidate, n = wanted, 1
    while candidate != path and (photo_dir / candidate).exists():
        n += 1
        candidate = f'{wanted}-{n}'
    return candidate


def make_dirs(photo_dir: Path, path: str) -> None:
    """Create path below photo_dir one level at a time; photo_dir itself is never created."""
    if not photo_dir.is_dir():
        raise PhotoDirMissing
    current = photo_dir
    for part in PurePosixPath(path).parts:
        current = current / part
        current.mkdir(exist_ok=True)


def local_filename(item: RemoteItem) -> str:
    return f'{sanitize_name(item.filename.rsplit(".", 1)[0])}_{item.id}.jpg'


def prune_empty(photo_dir: Path, base: str, path: str) -> None:
    """Remove path's parent folders, nearest first, while each is empty and lies below photo_dir/base.

    Only folders a move or removal just emptied are passed here; anything in a folder (a hidden
    file, a NAS's @eaDir) keeps it.
    """
    root = (photo_dir / base).resolve() if base else photo_dir.resolve()
    current = (photo_dir / path).parent
    while True:
        resolved = current.resolve()
        if resolved == root or not resolved.is_relative_to(root):
            return
        try:
            current.rmdir()  # only succeeds on an empty folder
        except OSError:
            return
        current = current.parent
