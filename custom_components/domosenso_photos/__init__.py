"""DomoSenso Photos: sync shared Synology Photos albums into a photo folder for a slideshow."""
from homeassistant.helpers import config_validation as cv

from . import albums
from .albums import DOMAIN

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup_entry(hass, entry):
    return await albums.async_setup_entry(hass, entry)


async def async_unload_entry(hass, entry):
    return await albums.async_unload_entry(hass, entry)


async def async_remove_entry(hass, entry):
    await albums.async_remove_entry(hass, entry)
