"""Diagnostics: counts and statuses only; no token, credential, file name, album name or photo value."""
from __future__ import annotations

from collections import Counter

from .albums import DATA as ALBUMS
from .lightroom import KIND as LIGHTROOM


async def async_get_config_entry_diagnostics(hass, entry) -> dict:
    if entry.data.get('kind', 'albums') == LIGHTROOM:
        manager = getattr(entry, 'runtime_data', None)
        data = manager.data if manager else {}
        catalog = data.get('catalog') or {}
        albums = catalog.get('albums') or []
        return {'kind': LIGHTROOM, 'loaded': manager is not None,
                'status': manager.public()['status'] if manager else None, 'error': data.get('error'),
                'asset_count': catalog.get('asset_count'),
                'albums': sum(1 for a in albums if not a.get('is_set')),
                'sets': sum(1 for a in albums if a.get('is_set')),
                # Field -> number of assets carrying it: how a library's metadata is confirmed.
                'coverage': dict(data.get('coverage') or {})}
    manager = hass.data.get(ALBUMS)
    records = list(manager.albums.values()) if manager else []
    return {'kind': 'albums', 'loaded': manager is not None, 'albums': len(records),
            'statuses': dict(Counter(str(r.get('status')) for r in records))}
