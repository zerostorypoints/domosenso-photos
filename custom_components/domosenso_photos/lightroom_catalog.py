"""Adobe Lightroom asset/album JSON -> plain dataclasses. No Home Assistant imports."""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CameraMeta:
    make: str | None
    model: str | None
    lens: str | None
    focal_mm: float | None
    focal35_mm: float | None
    f_number: float | None
    exposure_s: float | None
    iso: int | None


@dataclass(frozen=True)
class Photo:
    asset_id: str
    rating: int  # 0 when unrated
    flag: str | None  # 'pick', 'reject' or None
    captured: datetime  # naive: the camera's wall time
    changed: str
    edited: bool
    album_ids: tuple[str, ...]
    meta: CameraMeta
    width: int | None = None
    height: int | None = None
    keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class Album:
    id: str
    name: str
    parent_id: str | None
    is_set: bool


def _latest(entries) -> dict:
    """ratings/reviews are keyed by the app that set them; the latest date counts."""
    if not isinstance(entries, dict):
        return {}
    items = [e for e in entries.values() if isinstance(e, dict)]
    return max(items, key=lambda e: str(e.get('date') or ''), default={})  # a non-text date must not break max


def _wall_time(value) -> datetime | None:
    """Local time as written, offset and fraction dropped."""
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).replace(tzinfo=None, microsecond=0)
    except (AttributeError, TypeError, ValueError):
        return None


def _number(value) -> float | None:
    try:
        if isinstance(value, (list, tuple)):
            return value[0] / value[1]
        return float(value)
    except (TypeError, ValueError, IndexError, ZeroDivisionError):
        return None


def _rating(value) -> int:
    """0 when unrated or unreadable."""
    number = _number(value)
    return int(number) if number is not None and math.isfinite(number) else 0


def _text(value) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _dimensions(payload: dict) -> tuple[int | None, int | None]:
    """Always none: see DIMENSIONS in docs/lightroom-api.md."""
    return None, None


def _keywords(payload: dict) -> tuple[str, ...]:
    """Always none: see KEYWORDS in docs/lightroom-api.md."""
    return ()


def to_photo(asset: dict, album_ids: Sequence[str]) -> Photo:
    pl = asset.get('payload') or {}
    xmp = pl.get('xmp') or {}
    tiff, exif, aux = xmp.get('tiff') or {}, xmp.get('exif') or {}, xmp.get('aux') or {}
    iso = _number(exif.get('ISOSpeedRatings'))
    meta = CameraMeta(
        make=_text(tiff.get('Make')), model=_text(tiff.get('Model')), lens=_text(aux.get('Lens')),
        focal_mm=_number(exif.get('FocalLength')), focal35_mm=_number(exif.get('FocalLengthIn35mmFilm')),
        f_number=_number(exif.get('FNumber')), exposure_s=_number(exif.get('ExposureTime')),
        iso=int(iso) if iso else None)
    captured = (_wall_time(pl.get('captureDate'))
                or _wall_time((pl.get('importSource') or {}).get('importTimestamp'))
                or _wall_time(pl.get('userCreated')) or _wall_time(asset.get('created'))
                or datetime(1970, 1, 1))
    develop = pl.get('develop') or {}
    flag = _latest(pl.get('reviews')).get('flag')
    width, height = _dimensions(pl)
    # docs/lightroom-api.md (CHANGED, EDITED): both stamps; develop.userUpdated is on unedited assets too
    changed = f"{develop.get('userUpdated') or ''}|{pl.get('userUpdated') or ''}"
    if changed == '|':
        changed = asset.get('updated') or ''
    from_defaults = develop.get('fromDefaults')
    edited = from_defaults is False or (from_defaults is None and bool(develop.get('userUpdated')))
    return Photo(
        asset_id=asset['id'],
        rating=_rating(_latest(pl.get('ratings')).get('rating')),
        flag=flag if flag in ('pick', 'reject') else None,
        captured=captured,
        changed=changed,
        edited=edited,
        album_ids=tuple(album_ids),
        meta=meta, width=width, height=height, keywords=_keywords(pl))


def to_album(resource: dict) -> Album:
    pl = resource.get('payload') or {}
    return Album(
        id=resource['id'], name=pl.get('name') or '',
        parent_id=(pl.get('parent') or {}).get('id'),
        is_set=resource.get('subtype') == 'collection_set')


def album_label(album_id: str, albums: dict[str, Album]) -> str:
    """'Set / Sub / Album'; an unknown id is returned as is."""
    parts, seen, cur = [], set(), album_id
    while cur in albums and cur not in seen:
        seen.add(cur)
        parts.append(albums[cur].name)
        cur = albums[cur].parent_id
    return ' / '.join(reversed(parts)) if parts else album_id


def photo_to_dict(p: Photo) -> dict:
    return {
        'asset_id': p.asset_id, 'rating': p.rating, 'flag': p.flag,
        'captured': p.captured.isoformat(), 'changed': p.changed, 'edited': p.edited,
        'album_ids': list(p.album_ids),
        'meta': {k: getattr(p.meta, k) for k in CameraMeta.__dataclass_fields__},
        'width': p.width, 'height': p.height, 'keywords': list(p.keywords),
    }


def photo_from_dict(d: dict) -> Photo:
    return Photo(
        asset_id=d['asset_id'], rating=d['rating'], flag=d['flag'],
        captured=datetime.fromisoformat(d['captured']), changed=d['changed'], edited=d['edited'],
        album_ids=tuple(d['album_ids']), meta=CameraMeta(**d['meta']),
        width=d.get('width'), height=d.get('height'), keywords=tuple(d.get('keywords') or ()))
