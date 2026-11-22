"""DomoSenso Photos: sync shared Synology Photos albums or an Adobe Lightroom library into a photo folder."""
from homeassistant.helpers import config_validation as cv

from . import albums, lightroom
from .albums import DOMAIN

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


def _module(entry):
    """Entries from before Lightroom carry no kind: they are the albums entry."""
    return lightroom if entry.data.get('kind', 'albums') == lightroom.KIND else albums


async def async_setup_entry(hass, entry):
    return await _module(entry).async_setup_entry(hass, entry)


async def async_unload_entry(hass, entry):
    return await _module(entry).async_unload_entry(hass, entry)


async def async_remove_entry(hass, entry):
    await _module(entry).async_remove_entry(hass, entry)
