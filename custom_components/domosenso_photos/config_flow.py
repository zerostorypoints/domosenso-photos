"""Config flow: a Synology albums entry (one per installation) or a Lightroom entry per Adobe account."""
import logging
import os
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import config_entry_oauth2_flow, selector
from homeassistant.util import dt as dt_util

from .albums import DATA as ALBUMS, DEFAULT_PHOTO_DIR, DEFAULTS, DOMAIN, FLOW_ACCEPTED, FLOW_ERRORS, AlbumError, format_host_map, parse_host_map
from .application_credentials import SCOPES
from .lightroom import DEFAULT_OPTIONS as LIGHTROOM_OPTIONS, KIND as LIGHTROOM, cached_library
from .lightroom_api import account_id
from .lightroom_catalog import album_label
from .lightroom_filter import FLAGS

_LOGGER = logging.getLogger(__name__)


def _kind(entry) -> str:
    return entry.data.get('kind', 'albums')


async def _photo_dir(hass, value: str) -> str | None:
    """The folder when it is an absolute path to an existing directory, else None."""
    path = value.strip()
    return path if os.path.isabs(path) and await hass.async_add_executor_job(os.path.isdir, path) else None


def _prefix(value: str | None) -> str | None:
    """A relative folder inside photo_dir ('' for none); None when it could leave photo_dir."""
    text = (value or '').strip().strip('/')
    parts = PurePosixPath(text).parts if text else ()
    if any(part in ('.', '..') for part in parts) or '\\' in text:
        return None
    return '/'.join(parts)


def _folder(photo_dir: str, prefix: str | None) -> str:
    """The folder photos land in, as shown to the user."""
    return f'{photo_dir.rstrip("/")}/{prefix}' if prefix else photo_dir


class PhotosConfigFlow(config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN):
    DOMAIN = DOMAIN
    VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        self._oauth_data: dict | None = None

    @property
    def logger(self) -> logging.Logger:
        return _LOGGER

    @property
    def extra_authorize_data(self) -> dict:
        return {'scope': SCOPES}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return LightroomOptionsFlow() if _kind(config_entry) == LIGHTROOM else AlbumsOptionsFlow()

    async def async_step_user(self, user_input=None):
        return self.async_show_menu(step_id='user', menu_options=['albums', 'lightroom'])

    async def async_step_albums(self,user_input=None):
        await self.async_set_unique_id('albums')
        self._abort_if_unique_id_configured()
        errors={}
        if user_input is not None:
            if path := await _photo_dir(self.hass, user_input['photo_dir']):
                return self.async_create_entry(title='DomoSenso Photos',data={'photo_dir':path})
            errors['photo_dir']='invalid_photo_dir'
        return self.async_show_form(step_id='albums',data_schema=vol.Schema({
            vol.Required('photo_dir',default=DEFAULT_PHOTO_DIR):selector.TextSelector()}),errors=errors)

    async def async_step_lightroom(self, user_input=None):
        return await self.async_step_pick_implementation()

    async def async_oauth_create_entry(self, data: dict):
        account = account_id(data['token'])
        if not account:
            return self.async_abort(reason='account_unknown')
        await self.async_set_unique_id(account)
        if self.source == config_entries.SOURCE_REAUTH:
            self._abort_if_unique_id_mismatch(reason='wrong_account')
            return self.async_update_reload_and_abort(self._get_reauth_entry(), data_updates=data)
        self._abort_if_unique_id_configured()
        self._oauth_data = data
        return await self.async_step_lightroom_folder()

    async def async_step_lightroom_folder(self, user_input=None):
        errors = {}
        if user_input is not None:
            path = await _photo_dir(self.hass, user_input['photo_dir'])
            prefix = _prefix(user_input.get('prefix'))
            if path is None:
                errors['photo_dir'] = 'invalid_photo_dir'
            if prefix is None:
                errors['prefix'] = 'invalid_prefix'
            if not errors:
                return self.async_create_entry(title='Lightroom', data={
                    'kind': LIGHTROOM, 'auth_implementation': self._oauth_data['auth_implementation'],
                    'token': self._oauth_data['token'], 'photo_dir': path, 'prefix': prefix},
                    options=dict(LIGHTROOM_OPTIONS))
        schema = vol.Schema({
            vol.Required('photo_dir', default=DEFAULT_PHOTO_DIR): selector.TextSelector(),
            vol.Optional('prefix', default=''): selector.TextSelector()})
        return self.async_show_form(step_id='lightroom_folder', errors=errors,
                                    data_schema=self.add_suggested_values_to_schema(schema, user_input) if user_input else schema)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        if user_input is None:
            return self.async_show_form(step_id='reauth_confirm')
        return await self.async_step_pick_implementation()


def _number(lo,hi,step=1,unit=None):
    # NumberSelectorConfig rejects unit_of_measurement=None; leave it out for unitless numbers.
    return selector.NumberSelector(selector.NumberSelectorConfig(min=lo,max=hi,step=step,mode=selector.NumberSelectorMode.BOX,**({'unit_of_measurement':unit} if unit else {})))

class AlbumsOptionsFlow(config_entries.OptionsFlow):
    """Album actions run on the loaded manager; only settings change the entry options."""

    def _manager(self):return self.hass.data.get(ALBUMS)

    def _done(self):return self.async_create_entry(data=dict(self.config_entry.options))

    def _choices(self):
        return [{'value':a['id'],'label':a['name'] or a['path'] or a['id']} for a in self._manager().public_list()]

    async def async_step_init(self,user_input=None):
        if not self._manager():return self.async_abort(reason='not_loaded')
        return self.async_show_menu(step_id='init',menu_options=['add_album','remove_album','sync_now','settings'])

    async def async_step_add_album(self,user_input=None):
        errors={}
        if user_input is not None:
            manager=self._manager()
            try:rec=await manager.add(user_input['url'],user_input.get('password'),user_input.get('path'))
            except AlbumError as err:errors['base']=err.code
            else:
                if rec['status'] in FLOW_ACCEPTED:return self._done()
                await manager.remove(rec['id'])  # the user is still in the form: keep nothing
                errors['base']=FLOW_ERRORS.get(rec['status'],'share_error')
        schema=vol.Schema({
            vol.Required('url'):selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.URL)),
            vol.Optional('password'):selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
            vol.Optional('path'):selector.TextSelector(),
        })
        shown={k:v for k,v in (user_input or {}).items() if k!='password'}
        return self.async_show_form(step_id='add_album',data_schema=self.add_suggested_values_to_schema(schema,shown),errors=errors)

    async def async_step_remove_album(self,user_input=None):
        if not (choices:=self._choices()):return self.async_abort(reason='no_albums')
        errors={}
        if user_input is not None:
            try:busy=await self._manager().remove(user_input['album_id'])
            except AlbumError as err:errors['base']=err.code
            else:
                if busy is None:return self._done()
                errors['base']='album_busy'
        return self.async_show_form(step_id='remove_album',data_schema=vol.Schema({
            vol.Required('album_id'):selector.SelectSelector(selector.SelectSelectorConfig(options=choices,mode=selector.SelectSelectorMode.LIST))}),errors=errors)

    async def async_step_sync_now(self,user_input=None):
        if not (choices:=self._choices()):return self.async_abort(reason='no_albums')
        if user_input is not None:
            # Runs in the background; the Albums sensor shows progress and the result.
            self.config_entry.async_create_background_task(self.hass,self._manager().sync(user_input.get('album_id'),user_input.get('confirm_deletions',False)),'domosenso_photos album sync now')
            return self._done()
        return self.async_show_form(step_id='sync_now',data_schema=vol.Schema({
            vol.Optional('album_id'):selector.SelectSelector(selector.SelectSelectorConfig(options=choices,mode=selector.SelectSelectorMode.DROPDOWN)),
            vol.Optional('confirm_deletions',default=False):selector.BooleanSelector()}))

    async def async_step_settings(self,user_input=None):
        opts={**DEFAULTS,**self.config_entry.options}
        errors={}
        if user_input is not None:
            try:host_map=parse_host_map(user_input.get('host_map',''))
            except ValueError:errors['host_map']='invalid_host_map'
            else:
                return self.async_create_entry(data={'interval_min':int(user_input['interval_min']),'max_edge':int(user_input['max_edge']),
                    'quality':int(user_input['quality']),'free_floor':int(round(float(user_input['free_floor_gb'])*1e9)),'host_map':host_map})
        schema=vol.Schema({
            vol.Required('interval_min',default=opts['interval_min']):_number(15,1440,1,'min'),
            vol.Required('max_edge',default=opts['max_edge']):_number(1024,8192,1,'px'),
            vol.Required('quality',default=opts['quality']):_number(60,95),
            vol.Required('free_floor_gb',default=opts['free_floor']/1e9):_number(0.1,1000,0.1,'GB'),
            vol.Optional('host_map',description={'suggested_value':format_host_map(opts['host_map'])}):selector.TextSelector(selector.TextSelectorConfig(multiline=True)),
        })
        # hassfest refuses URLs inside translation strings; the example origin comes in as a placeholder.
        return self.async_show_form(step_id='settings',data_schema=self.add_suggested_values_to_schema(schema,user_input) if user_input else schema,errors=errors,
                                    description_placeholders={'example_origin':'https://photos.example.com'})


RATINGS = [str(n) for n in range(6)]
FILTER_KEYS = ('ratings', 'flag', 'albums_include', 'albums_exclude', 'include_unfiled', 'captured_from',
               'captured_to', 'captured_last_years', 'edited_only', 'equipment_include', 'equipment_exclude')


def _terms(text: str | None) -> list[str]:
    return [t.strip() for t in (text or '').split(',') if t.strip()]


def _years(value) -> int | None:
    """captured_last_years as a whole number 1-50; None when it is not one."""
    try:
        years = float(value)
    except (TypeError, ValueError):
        return None
    return int(years) if years.is_integer() and 1 <= years <= 50 else None


def _when(epoch: int | None) -> str:
    return dt_util.as_local(dt_util.utc_from_timestamp(epoch)).strftime('%Y-%m-%d %H:%M') if epoch else '-'


class LightroomOptionsFlow(config_entries.OptionsFlow):
    """Sync now, the filter with its preview, and settings; the loaded manager answers from its cache.

    Step ids carry `lightroom_` where the albums flow already uses the name, since both share the
    options translations. Orientation and keyword fields are not offered: no library has shown them
    yet (the filter still honours them when set in the options).
    """

    def __init__(self) -> None:
        self._pending: dict | None = None
        self._folder_change: tuple[str, str, dict] | None = None

    def _manager(self):
        return getattr(self.config_entry, 'runtime_data', None)

    def _options(self) -> dict:
        return {**LIGHTROOM_OPTIONS, **self.config_entry.options}

    async def async_step_init(self, user_input=None):
        return await self.async_step_lightroom()

    async def async_step_lightroom(self, user_input=None):
        if self._manager() is None:
            return self.async_abort(reason='not_loaded')
        return self.async_show_menu(step_id='lightroom', menu_options=['sync_now', 'filter', 'settings'])

    async def async_step_sync_now(self, user_input=None):
        return await self.async_step_lightroom_sync_now()

    async def async_step_lightroom_sync_now(self, user_input=None):
        if user_input is not None:
            # Runs in the background; the Lightroom sensor shows progress and the result.
            self.config_entry.async_create_background_task(
                self.hass, self._manager().sync(user_input.get('confirm', False)), 'domosenso_photos lightroom sync now')
            return self.async_create_entry(data=dict(self.config_entry.options))
        return self.async_show_form(step_id='lightroom_sync_now', data_schema=vol.Schema({
            vol.Optional('confirm', default=False): selector.BooleanSelector()}))

    async def async_step_settings(self, user_input=None):
        return await self.async_step_lightroom_settings()

    async def async_step_lightroom_settings(self, user_input=None):
        opts, data = self._options(), self.config_entry.data
        errors: dict[str, str] = {}
        shown = {'photo_dir': data['photo_dir'], 'prefix': data.get('prefix') or '',
                 'interval_min': opts['interval_min'], 'max_edge': opts['max_edge'], 'quality': opts['quality'],
                 'free_floor_gb': opts['free_floor'] / 1e9}
        if user_input is not None:
            shown = {**shown, **user_input}
            path = await _photo_dir(self.hass, user_input['photo_dir'])
            prefix = _prefix(user_input.get('prefix'))
            if path is None:
                errors['photo_dir'] = 'invalid_photo_dir'
            if prefix is None:
                errors['prefix'] = 'invalid_prefix'
            if not errors:
                new_options = {
                    **self.config_entry.options, 'interval_min': int(user_input['interval_min']),
                    'max_edge': int(user_input['max_edge']), 'quality': int(user_input['quality']),
                    'free_floor': int(round(float(user_input['free_floor_gb']) * 1e9))}
                if path == data['photo_dir'] and prefix == (data.get('prefix') or ''):
                    return self.async_create_entry(data=new_options)
                self._folder_change = (path, prefix, new_options)
                return await self.async_step_lightroom_folder_confirm()
        return self._settings_form(shown, errors)

    def _settings_form(self, shown: dict, errors: dict[str, str]):
        schema = vol.Schema({
            vol.Required('photo_dir'): selector.TextSelector(),
            vol.Optional('prefix'): selector.TextSelector(),
            vol.Required('interval_min'): _number(60, 1440, 1, 'min'),
            vol.Required('max_edge'): _number(1024, 8192, 1, 'px'),
            vol.Required('quality'): _number(60, 95),
            vol.Required('free_floor_gb'): _number(0.1, 1000, 0.1, 'GB')})
        return self.async_show_form(step_id='lightroom_settings', errors=errors,
                                    data_schema=self.add_suggested_values_to_schema(schema, shown))

    async def async_step_lightroom_folder_confirm(self, user_input=None):
        """Every photo is downloaded again into the new folder; the old one is left as it is.

        A running sync is stopped: the reload cancels it (each photo's record is written with the
        photo, so nothing is half done), and the new manager syncs into the new folder at once.
        """
        path, prefix, new_options = self._folder_change
        if user_input is not None:
            # Data and options in one update: the update listener reloads the entry once.
            self.hass.config_entries.async_update_entry(
                self.config_entry, data={**self.config_entry.data, 'photo_dir': path, 'prefix': prefix},
                options=new_options)
            return self.async_create_entry(data=new_options)
        data = self.config_entry.data
        return self.async_show_form(step_id='lightroom_folder_confirm', data_schema=vol.Schema({}),
                                    description_placeholders={'old': _folder(data['photo_dir'], data.get('prefix')),
                                                              'new': _folder(path, prefix)})

    def _album_choices(self, chosen: set[str]) -> list[dict]:
        cached = cached_library(self._manager().data.get('catalog'))
        albums = cached[1] if cached else {}
        choices = sorted(({'value': i, 'label': album_label(i, albums)} for i in albums),
                         key=lambda c: c['label'].casefold())
        # An id gone from the library stays selectable (and ignored by the filter) until removed here.
        return choices + [{'value': i, 'label': f'missing album ({i})'} for i in sorted(chosen - set(albums))]

    def _filter_schema(self, shown: dict) -> vol.Schema:
        chosen = set(shown.get('albums_include') or ()) | set(shown.get('albums_exclude') or ())
        albums = selector.SelectSelector(selector.SelectSelectorConfig(
            options=self._album_choices(chosen), multiple=True, mode=selector.SelectSelectorMode.DROPDOWN))
        schema = vol.Schema({
            vol.Optional('ratings'): selector.SelectSelector(selector.SelectSelectorConfig(
                options=RATINGS, multiple=True, mode=selector.SelectSelectorMode.LIST, translation_key='rating')),
            vol.Required('flag'): selector.SelectSelector(selector.SelectSelectorConfig(
                options=list(FLAGS), mode=selector.SelectSelectorMode.DROPDOWN, translation_key='flag')),
            vol.Optional('albums_include'): albums,
            vol.Optional('albums_exclude'): albums,
            vol.Required('include_unfiled'): selector.BooleanSelector(),
            vol.Optional('captured_from'): selector.DateSelector(),
            vol.Optional('captured_to'): selector.DateSelector(),
            vol.Optional('captured_last_years'): selector.NumberSelector(selector.NumberSelectorConfig(
                step=1, mode=selector.NumberSelectorMode.BOX)),
            vol.Required('edited_only'): selector.BooleanSelector(),
            vol.Optional('equipment_include'): selector.TextSelector(),
            vol.Optional('equipment_exclude'): selector.TextSelector()})
        return self.add_suggested_values_to_schema(schema, shown)

    async def async_step_filter(self, user_input=None):
        errors = {}
        if user_input is not None:
            years = user_input.get('captured_last_years')
            start, end = user_input.get('captured_from') or None, user_input.get('captured_to') or None
            if not user_input.get('ratings'):
                errors['ratings'] = 'ratings_empty'  # no rating passes: nothing would ever be synced
            if years not in (None, '') and _years(years) is None:
                errors['captured_last_years'] = 'invalid_years'
            if start and end and start > end:  # ISO dates compare as text
                errors['captured_to'] = 'invalid_range'
            if not errors:
                self._pending = {**self.config_entry.options,
                                 'ratings': sorted(int(r) for r in user_input.get('ratings') or ()),
                                 'flag': user_input['flag'],
                                 'albums_include': sorted(user_input.get('albums_include') or ()),
                                 'albums_exclude': sorted(user_input.get('albums_exclude') or ()),
                                 'include_unfiled': bool(user_input['include_unfiled']),
                                 'captured_from': start, 'captured_to': end,
                                 'captured_last_years': None if years in (None, '') else _years(years),
                                 'edited_only': bool(user_input['edited_only']),
                                 'equipment_include': _terms(user_input.get('equipment_include')),
                                 'equipment_exclude': _terms(user_input.get('equipment_exclude'))}
                return await self.async_step_filter_preview()
            shown = user_input
        else:
            opts = self._pending or self._options()
            shown = {k: opts.get(k) for k in FILTER_KEYS if opts.get(k) not in (None, '')}
            shown['ratings'] = [str(r) for r in opts.get('ratings') or ()]
            shown['equipment_include'] = ', '.join(opts.get('equipment_include') or ())
            shown['equipment_exclude'] = ', '.join(opts.get('equipment_exclude') or ())
        return self.async_show_form(step_id='filter', data_schema=self._filter_schema(shown), errors=errors)

    async def async_step_filter_preview(self, user_input=None):
        result = await self._manager().preview(self._pending)
        if result['cache_date'] is None:  # no library read yet: nothing to count, Save still applies
            return self.async_show_menu(step_id='filter_no_library', menu_options=['save', 'filter'])
        if result['error']:  # photo folder missing: counting would show every photo as new
            return self.async_show_menu(step_id='filter_no_folder', menu_options=['save', 'filter'])
        top = '\n'.join(f"- {t['folder']}: +{t['add']} / -{t['remove']}" for t in result['top']) or '-'
        return self.async_show_menu(step_id='filter_preview', menu_options=['save', 'filter'], description_placeholders={
            'kept': str(result['kept']), 'add': str(result['add']), 'remove': str(result['remove']),
            'move': str(result['move']), 'hold': result['hold'] or '-', 'top': top,
            'cache_date': _when(result['cache_date'])})

    # A menu's step id must have a handler (HA raises UnknownStep otherwise); nothing submits to these.
    async def async_step_filter_no_library(self, user_input=None):
        return await self.async_step_filter_preview()

    async def async_step_filter_no_folder(self, user_input=None):
        return await self.async_step_filter_preview()

    async def async_step_save(self, user_input=None):
        # The update listener reloads the entry, and the new manager syncs at once.
        return self.async_create_entry(data=self._pending or dict(self.config_entry.options))
