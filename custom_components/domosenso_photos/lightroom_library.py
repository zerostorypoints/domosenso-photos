"""Read a whole Lightroom library and keep the run's skip list (no Home Assistant import)."""
from __future__ import annotations

from dataclasses import dataclass

from .lightroom_api import LightroomClient, RenditionUnavailable, Unreachable
from .lightroom_catalog import Album, Photo, photo_to_dict, to_album, to_photo

SKIP_REST_S = 86400
OUTAGE_RUN = 5  # renditions in a row failing for network or 5xx reasons: Adobe is down, not the photos
# Fields whose presence is counted over the raw assets (diagnostics shows the counts, never values).
COVERAGE_FIELDS = ('payload.ratings', 'payload.reviews', 'payload.captureDate', 'payload.develop.userUpdated',
                   'payload.xmp.tiff.Make', 'payload.xmp.aux.Lens')


@dataclass(frozen=True)
class Library:
    catalog: str
    photos: list[Photo]
    albums: dict[str, Album]
    asset_count: int
    coverage: dict[str, int]


def _present(asset: dict, dotted: str) -> bool:
    value = asset
    for part in dotted.split('.'):
        if not isinstance(value, dict):
            return False
        value = value.get(part)
    return value not in (None, '', {}, [])


def coverage(raw_assets: list[dict]) -> dict[str, int]:
    return {f: sum(1 for a in raw_assets if _present(a, f)) for f in COVERAGE_FIELDS}


async def read_library(client: LightroomClient) -> Library:
    """Catalog id, albums, each collection's members, then every image asset; raises on any failure."""
    catalog = await client.catalog_id()
    albums = {a.id: a for a in map(to_album, await client.albums(catalog))}
    members: dict[str, list[str]] = {}
    for album in albums.values():
        if not album.is_set:  # a set holds albums, not assets
            for asset_id in await client.album_asset_ids(catalog, album.id):
                members.setdefault(asset_id, []).append(album.id)
    raw = await client.assets(catalog)
    photos = [to_photo(a, members.get(a['id'], ())) for a in raw]
    return Library(catalog, photos, albums, len(raw), coverage(raw))


def cache(library: Library, now: int) -> dict:
    """The trimmed library the store keeps for the preview and the album picker."""
    return {'read_at': now, 'asset_count': library.asset_count,
            'photos': [photo_to_dict(p) for p in library.photos],
            'albums': [{'id': a.id, 'name': a.name, 'parent_id': a.parent_id, 'is_set': a.is_set}
                       for a in library.albums.values()]}


def resting(skipped: dict[str, str] | None, skipped_at: int | None, photos: list[Photo], now: int) -> set[str]:
    """Skipped assets tried less than a day ago whose `changed` is the same: not fetched this run."""
    if not skipped or skipped_at is None or now - skipped_at >= SKIP_REST_S:
        return set()
    return {p.asset_id for p in photos if p.asset_id in skipped and skipped[p.asset_id] == p.changed}


def next_skips(skipped_now: list[str], photos: list[Photo], tried_now: bool, skipped_at: int | None,
               now: int) -> tuple[dict[str, str], int | None]:
    """The skip list to store and when it was last tried: now if this run tried one, else the earlier time."""
    changed = {p.asset_id: p.changed for p in photos}
    skipped = {a: changed.get(a, '') for a in skipped_now}
    if not skipped:
        return {}, None
    return skipped, (now if tried_now or skipped_at is None else skipped_at)


class Renditions:
    """One run's rendition fetches: a failed photo is skipped (None); OUTAGE_RUN transient failures in a
    row end the run as unreachable, so an outage does not mark the whole library skipped."""

    def __init__(self, client: LightroomClient, catalog: str, max_edge: int):
        self._client, self._catalog, self._max_edge = client, catalog, max_edge
        self._in_a_row = 0

    async def __call__(self, asset_id: str) -> bytes | None:
        try:
            raw = await self._client.image(self._catalog, asset_id, self._max_edge)
        except RenditionUnavailable as err:
            self._in_a_row = self._in_a_row + 1 if err.transient else 0
            if self._in_a_row >= OUTAGE_RUN:
                raise Unreachable(f'{OUTAGE_RUN} renditions in a row failed') from err
            return None
        self._in_a_row = 0
        return raw
