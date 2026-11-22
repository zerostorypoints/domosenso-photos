"""Sensors: the Albums sensor (number of synced albums) and one sensor per Lightroom library."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .albums import DATA, SIGNAL
from .lightroom import DOMAIN, KIND as LIGHTROOM, signal as lightroom_signal

LIGHTROOM_ATTRIBUTES = ('photos', 'folders', 'last_sync', 'last_success', 'added', 'updated', 'moved', 'removed',
                        'skipped_count', 'error', 'signin_expires', 'cache_date', 'path_collisions',
                        'ambiguous_folders', 'phase', 'done', 'total', 'progress')


async def async_setup_entry(hass, entry, async_add_entities):
    if entry.data.get('kind', 'albums') == LIGHTROOM:
        # A separate progress sensor existed briefly; its progress is now an attribute here.
        registry = er.async_get(hass)
        if old := registry.async_get_entity_id('sensor', DOMAIN, f'{entry.entry_id}_lightroom_progress'):
            registry.async_remove(old)
        async_add_entities([LightroomSensor(entry)])
    else:
        async_add_entities([AlbumsSensor(hass.data[DATA])])


class AlbumsSensor(SensorEntity):
    _attr_name = 'Albums'
    _attr_unique_id = 'domosenso_photos_albums'
    _attr_icon = 'mdi:image-multiple'
    _attr_should_poll = False
    _unrecorded_attributes = frozenset({'albums'})

    def __init__(self, manager):
        self._manager = manager
        self.entity_id = 'sensor.domosenso_photos_albums'

    @property
    def native_value(self) -> int:
        return len(self._manager.albums)

    @property
    def extra_state_attributes(self) -> dict:
        return {'albums': self._manager.public_list()}

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL, self.async_write_ha_state))


class LightroomSensor(SensorEntity):
    """State = the library's sync status; Home Assistant numbers a second library's entity id."""

    _attr_name = 'DomoSenso Photos Lightroom'
    _attr_icon = 'mdi:image-filter-hdr'
    _attr_should_poll = False
    _unrecorded_attributes = frozenset({'folders', 'path_collisions', 'ambiguous_folders'})

    def __init__(self, entry):
        self._entry = entry
        self._attr_unique_id = f'{entry.entry_id}_lightroom'

    @property
    def native_value(self) -> str | None:
        return self._entry.runtime_data.public()['status']

    @property
    def extra_state_attributes(self) -> dict:
        public = self._entry.runtime_data.public()
        return {k: public[k] for k in LIGHTROOM_ATTRIBUTES}

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, lightroom_signal(self._entry.entry_id),
                                                      self.async_write_ha_state))

