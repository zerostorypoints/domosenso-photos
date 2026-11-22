"""Lightroom config entry: OAuth2 session, library read, timer and the sync of one Adobe account."""
from __future__ import annotations

import asyncio
import errno
import logging
import time
from datetime import timedelta
from pathlib import Path

import aiohttp
import voluptuous as vol
from PIL import Image, UnidentifiedImageError
from homeassistant.core import SupportsResponse, callback
from homeassistant.exceptions import (ConfigEntryError, ConfigEntryNotReady, OAuth2TokenRequestError,
                                      OAuth2TokenRequestReauthError, ServiceValidationError)
from homeassistant.helpers import config_entry_oauth2_flow, config_validation as cv, entity_registry as er, issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import album_image, lightroom_api, lightroom_library
from .album_layout import PhotoDirMissing
from .lightroom_api import AuthRefused, LightroomClient, LightroomError, Unreachable, account_id, signin_expires
from .lightroom_catalog import Album, photo_from_dict
from .lightroom_exif import exif_adder
from .lightroom_filter import Filter, Placed, filter_from_options, filter_to_options, place
from .lightroom_sync import DiskFull, collisions, execute, owner_for, plan, preview, read_owned
from .transport import make_transport

_LOGGER = logging.getLogger(__name__)
DOMAIN = 'domosenso_photos'
KIND = 'lightroom'
PLATFORMS = ['sensor']
SERVICES = ('lightroom_sync', 'lightroom_preview')
# Entry ids whose options were just saved: the reloaded manager syncs at once, not after the start delay.
SYNC_ON_START = 'domosenso_photos_lightroom_sync_on_start'
SIGNIN_WARN = timedelta(days=3)
SERVICE_USERS = 'domosenso_photos_lightroom_service_users'  # loaded entries counting on the actions
DEFAULT_OPTIONS = {**filter_to_options(Filter()), 'interval_min': 360, 'max_edge': 2560, 'quality': 85,
                   'free_floor': 1_000_000_000}
STARTUP_DELAY_S = 60
PROGRESS_EVERY_S = 5  # at most one progress update per 5 s reaches the state machine
UNLOAD_WAIT_S = 30
_BAD_IMAGE = (UnidentifiedImageError, Image.DecompressionBombError, album_image.HeifUnavailable, OSError, ValueError)
_BAD_RESPONSE = (LightroomError, KeyError, IndexError, AttributeError, TypeError, ValueError)


def store_key(entry_id: str) -> str:
    return f'domosenso_photos.lightroom.{entry_id}'


def signal(entry_id: str) -> str:
    return f'domosenso_photos_lightroom_updated_{entry_id}'


def cached_library(catalog: dict | None) -> tuple[list, dict[str, Album]] | None:
    """Photos and albums from the stored catalog cache; None when no library has been read yet."""
    if not catalog:
        return None
    albums = {a['id']: Album(a['id'], a['name'], a.get('parent_id'), bool(a.get('is_set')))
              for a in catalog.get('albums') or ()}
    return [photo_from_dict(p) for p in catalog.get('photos') or ()], albums


class _Failed(Exception):
    def __init__(self, status: str, error: str | None = None):
        super().__init__(status)
        self.status, self.error = status, error


class LightroomManager:
    def __init__(self, hass, entry, session: config_entry_oauth2_flow.OAuth2Session):
        self.hass, self.entry, self.oauth = hass, entry, session
        self.photo_dir = Path(entry.data['photo_dir'])
        self.prefix = entry.data.get('prefix') or ''
        self.options_seen = dict(entry.options)  # a reload follows only an options change, not a token refresh
        opts = {**DEFAULT_OPTIONS, **entry.options}
        self.filter = filter_from_options(opts)
        self.interval = int(opts['interval_min'])
        self.max_edge, self.quality = int(opts['max_edge']), int(opts['quality'])
        self.free_floor = int(opts['free_floor'])
        # Folders belong to the Adobe account, so a removed and re-added entry takes them back.
        self.owner = owner_for(entry.unique_id or account_id(entry.data['token']))
        self.store = Store(hass, 1, store_key(entry.entry_id))
        self.data: dict = {}
        self.lock = asyncio.Lock()
        # Own session, not the albums one; cookies are not needed by Lightroom.
        self.http = async_create_clientsession(hass, cookie_jar=aiohttp.DummyCookieJar())
        self.client = LightroomClient(make_transport(self.http, with_headers=True),
                                      session.implementation.client_id, self._token,
                                      base=lightroom_api.API_BASE)  # read here so a smoke can point it at a fake
        self.closing = False
        self._unsubs: list = []
        self._task: asyncio.Task | None = None
        self._running: set[asyncio.Task] = set()
        # In memory only: how far the running sync is (all None outside a sync).
        self.progress: dict = {'phase': None, 'done': None, 'total': None}
        self._progress_sent = 0.0
        self._last_run_complete = False

    async def _token(self) -> str:
        await self.oauth.async_ensure_token_valid()
        return self.oauth.token['access_token']

    async def async_load(self) -> None:
        self.data = await self.store.async_load() or {}
        self._last_run_complete = self.data.get('status') == 'ok'  # as before the restart

    def start(self) -> None:
        self._issues()
        saved = self.hass.data.get(SYNC_ON_START, set())
        delay = 0 if self.entry.entry_id in saved else STARTUP_DELAY_S
        saved.discard(self.entry.entry_id)
        self._unsubs = [
            async_call_later(self.hass, delay, self._tick),
            async_track_time_interval(self.hass, self._tick, timedelta(minutes=self.interval),
                                      name='domosenso_photos lightroom', cancel_on_shutdown=True)]

    async def async_close(self) -> None:
        """Stop timers, cancel a running sync and wait for it (bounded); nothing is saved after."""
        self.closing = True
        for unsub in self._unsubs:
            unsub()
        tasks = [t for t in (self._task, *self._running) if t and not t.done()]
        for task in tasks:
            task.cancel()
        try:
            async with asyncio.timeout(UNLOAD_WAIT_S):
                await asyncio.gather(*tasks, return_exceptions=True)
                while self.lock.locked():
                    await asyncio.sleep(0.2)
        except TimeoutError:
            _LOGGER.warning('Lightroom sync still running after %s s', UNLOAD_WAIT_S)
        # detach, not close: the connector is Home Assistant's shared one (HA also detaches on unload).
        self.http.detach()

    def public(self) -> dict:
        d, counts = self.data, self.data.get('counts') or {}
        expires = signin_expires(self.oauth.token)
        return {'entry_id': self.entry.entry_id,
                'status': 'syncing' if self.lock.locked() else d.get('status'), 'error': d.get('error'),
                'hold': d.get('hold'), 'photos': d.get('photos', 0), 'folders': d.get('folders') or [],
                'last_sync': d.get('last_sync'), 'last_success': d.get('last_success'),
                **{k: counts.get(k, 0) for k in ('added', 'updated', 'moved', 'removed')},
                'skipped_count': len(d.get('skipped') or {}),
                'signin_expires': expires.isoformat() if expires else None,
                'cache_date': (d.get('catalog') or {}).get('read_at'),
                'path_collisions': list(d.get('collisions') or ()),
                'ambiguous_folders': [p for a in d.get('ambiguous') or () for p in a['paths']],
                **self.progress, 'progress': self._percent()}

    def _percent(self) -> int | None:
        if not self.lock.locked():
            return 100 if self._last_run_complete else None
        phase, done, total = self.progress['phase'], self.progress['done'], self.progress['total']
        if phase is None or total is None:
            return None
        if phase == 'reading_library':  # an estimate from the last read: never 100 before it ends
            return min(99, done * 100 // total) if total else None
        return 100 if total == 0 else done * 100 // total

    def _report(self, phase: str, done: int, total: int | None) -> None:
        """Progress from the running sync; pushed at a phase change, at the end, else every 5 s."""
        changed = phase != self.progress['phase']
        self.progress = {'phase': phase, 'done': done, 'total': total}
        now = time.monotonic()
        if changed or done == total or now - self._progress_sent >= PROGRESS_EVERY_S:
            self._progress_sent = now
            async_dispatcher_send(self.hass, signal(self.entry.entry_id))

    async def preview(self, options: dict | None = None) -> dict:
        """What a sync with these options (default: the entry's) would do, from the cache only."""
        catalog = self.data.get('catalog')
        cached = cached_library(catalog)
        out = {'entry_id': self.entry.entry_id, 'cache_date': (catalog or {}).get('read_at'), 'error': None}
        if cached is None:
            return {**out, 'kept': 0, 'add': 0, 'remove': 0, 'move': 0, 'hold': None, 'top': []}
        if not await self._run_blocking(self.photo_dir.is_dir):  # unmounted: every photo would look new
            return {**out, 'error': 'photo_dir_missing'}
        photos, albums = cached
        chosen = filter_from_options({**DEFAULT_OPTIONS, **self.entry.options, **(options or {})})
        placed = place(photos, chosen, albums, self.prefix, dt_util.now().date())
        owned = await self._run_blocking(lambda: read_owned(self.photo_dir, self.prefix, self.owner))
        return {**out, **preview(placed, owned, catalog.get('asset_count'))}

    async def sync(self, confirm: bool = False) -> dict:
        """One sync at a time; a call while one runs returns the state shown as syncing."""
        if self.closing:
            return self.public()
        if self.lock.locked():
            return {**self.public(), 'status': 'syncing'}
        task = self.hass.async_create_task(self._sync_locked(confirm), 'domosenso_photos lightroom sync')
        self._running.add(task)
        task.add_done_callback(self._running.discard)
        return await task

    def _run_blocking(self, fn):
        return self.hass.async_add_executor_job(fn)

    async def _sync_locked(self, confirm: bool) -> dict:
        if self.lock.locked():
            return {**self.public(), 'status': 'syncing'}
        async with self.lock:
            async_dispatcher_send(self.hass, signal(self.entry.entry_id))  # shows syncing
            # Only the hold that exists now, and that the user saw, is confirmed; any other hold stays.
            held = self.data.get('hold') if self.data.get('status') == 'awaiting_confirmation' else None
            now = int(time.time())
            try:
                update = await self._sync_body(held if confirm else None, now)
            except _Failed as err:
                update = {'status': err.status, 'error': err.error}
            except PhotoDirMissing:
                update = {'status': 'error', 'error': 'photo_dir_missing'}
            except (OAuth2TokenRequestReauthError, AuthRefused):
                self.entry.async_start_reauth(self.hass)
                update = {'status': 'error', 'error': 'auth_failed'}
            except (OAuth2TokenRequestError, aiohttp.ClientError, TimeoutError):
                update = {'status': 'unreachable', 'error': 'signin_unreachable'}
            except Unreachable as err:
                update = {'status': 'unreachable', 'error': err.code}
            except DiskFull:
                update = {'status': 'disk_full', 'error': None}
            except OSError as err:
                update = ({'status': 'disk_full', 'error': None} if err.errno == errno.ENOSPC
                          else {'status': 'error', 'error': 'write_failed'})
            except _BAD_RESPONSE as err:
                _LOGGER.debug('Lightroom answered unexpectedly: %s', type(err).__name__)
                update = {'status': 'error', 'error': 'unexpected_response'}
            except Exception as err:  # noqa: BLE001 - a bug must not stop the timer
                _LOGGER.error('Lightroom sync failed unexpectedly: %s', type(err).__name__)
                update = {'status': 'error', 'error': 'unexpected'}
            self.progress = {'phase': None, 'done': None, 'total': None}
            self._last_run_complete = update['status'] == 'ok'
            self.data.update(update, last_sync=now)
            if update['status'] in ('ok', 'awaiting_confirmation'):
                self.data['last_success'] = now
            await self._save()
        if not self.closing:
            async_dispatcher_send(self.hass, signal(self.entry.entry_id))  # the lock is free: no longer syncing
        return self.public()

    async def _sync_body(self, confirm: str | None, now: int) -> dict:
        if not await self._run_blocking(self.photo_dir.is_dir):  # unmounted storage: touch nothing
            raise PhotoDirMissing
        await self.oauth.async_ensure_token_valid()
        # Nothing in the photo folder is written until the whole library has been read.
        # Reading a large library takes minutes: count its listing pages against the last read's.
        expected, pages = self.data.get('library_pages'), 0
        self._report('reading_library', 0, expected)

        def page_read() -> None:
            nonlocal pages
            pages += 1
            self._report('reading_library', pages, expected)

        self.client.on_page = page_read
        try:
            library = await lightroom_library.read_library(self.client)
        finally:
            self.client.on_page = None
        self.data.update(catalog=lightroom_library.cache(library, now), coverage=library.coverage,
                         library_pages=pages)
        await self._save()

        placed = place(library.photos, self.filter, library.albums, self.prefix, dt_util.now().date())
        owned = await self._run_blocking(lambda: read_owned(self.photo_dir, self.prefix, self.owner))
        lr_plan = plan(placed, owned, library.asset_count, confirm)
        self.data['ambiguous'] = [{'key': key, 'paths': paths} for key, paths in lr_plan.ambiguous]

        resting = lightroom_library.resting(self.data.get('skipped'), self.data.get('skipped_at'),
                                            library.photos, now)
        tried = []
        rendition = lightroom_library.Renditions(self.client, library.catalog, self.max_edge)

        async def fetch(p: Placed) -> bytes | None:
            if p.photo.asset_id in resting:
                return None  # skipped less than a day ago and unchanged: no request
            tried.append(p.photo.asset_id)
            raw = await rendition(p.photo.asset_id)  # raises Unreachable on an outage
            if raw is None:
                return None
            try:
                return await self.hass.async_add_executor_job(
                    album_image.normalize, raw, self.max_edge, self.quality, exif_adder(p.photo), False)
            except _BAD_IMAGE:
                return None

        run = await execute(lr_plan, self.photo_dir, self.owner, fetch, self._run_blocking, self.free_floor,
                            dt_util.get_default_time_zone(), now, progress=self._report,
                            prefix=self.prefix)
        skipped, skipped_at = lightroom_library.next_skips(
            run.skipped, library.photos, any(a in tried for a in run.skipped), self.data.get('skipped_at'), now)
        names = {a.id: a.name for a in library.albums.values()}
        after = await self._run_blocking(lambda: read_owned(self.photo_dir, self.prefix, self.owner))
        collided = collisions(placed, after)
        folders = [{'key': f.key, 'name': names.get(f.key, f.key), 'path': f.path, 'photos': len(f.items)}
                   for f in sorted(after.values(), key=lambda f: f.path)]
        return {'status': 'awaiting_confirmation' if lr_plan.hold else 'ok', 'error': None, 'hold': lr_plan.hold,
                'counts': {'added': run.added, 'updated': run.updated, 'moved': run.moved, 'removed': run.removed},
                'skipped': skipped, 'skipped_at': skipped_at, 'collisions': collided,
                'photos': sum(f['photos'] for f in folders), 'folders': folders}

    @callback
    def _tick(self, _now) -> None:
        if not self.closing and (self._task is None or self._task.done()) and not self.lock.locked():
            self._task = self.entry.async_create_background_task(self.hass, self.sync(),
                                                                 'domosenso_photos lightroom sync')

    async def _save(self) -> None:
        if self.closing:  # a closed manager must not overwrite what its successor saves
            return
        await self.store.async_save(self.data)
        self._issues()
        async_dispatcher_send(self.hass, signal(self.entry.entry_id))

    def _entity_id(self) -> str:
        return (er.async_get(self.hass).async_get_entity_id('sensor', DOMAIN, f'{self.entry.entry_id}_lightroom')
                or 'sensor.domosenso_photos_lightroom')

    def _issues(self) -> None:
        # Issues go into every diagnostics download: placeholders carry counts, never folder paths
        # (they come from album names); the paths are in the sensor's attributes.
        d, prefix = self.data, f'lightroom_{self.entry.entry_id}'
        public = self.public()
        expires = signin_expires(self.oauth.token)
        entity = self._entity_id()
        wanted = {
            'awaiting_confirmation': ({'reason': d.get('hold') or ''}
                                      if d.get('status') == 'awaiting_confirmation' else None),
            'path_collision': ({'count': str(len(public['path_collisions'])), 'entity': entity}
                               if public['path_collisions'] else None),
            'ambiguous_folders': ({'count': str(len(public['ambiguous_folders'])), 'entity': entity}
                                  if public['ambiguous_folders'] else None),
            'signin_expiring': ({'expires': dt_util.as_local(expires).strftime('%Y-%m-%d %H:%M')}
                                if expires and expires < dt_util.utcnow() + SIGNIN_WARN else None),
        }
        for key, placeholders in wanted.items():
            issue_id = f'{prefix}_{key}'
            if placeholders is None:
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            else:
                ir.async_create_issue(self.hass, DOMAIN, issue_id, is_fixable=False,
                                      severity=ir.IssueSeverity.WARNING, translation_key=f'lightroom_{key}',
                                      translation_placeholders={'title': self.entry.title, **placeholders})


async def _options_changed(hass, entry) -> None:
    """Reload for an options or folder change only; a refreshed token also updates the entry."""
    manager = getattr(entry, 'runtime_data', None)
    if (manager is None or dict(entry.options) != manager.options_seen
            or Path(entry.data['photo_dir']) != manager.photo_dir
            or (entry.data.get('prefix') or '') != manager.prefix):
        hass.data.setdefault(SYNC_ON_START, set()).add(entry.entry_id)  # "after Save": sync at once
        await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass, entry) -> bool:
    try:
        implementation = await config_entry_oauth2_flow.async_get_config_entry_implementation(hass, entry)
    except ValueError as err:  # the application credential was removed: retrying will not help
        raise ConfigEntryError('The Lightroom application credential is missing') from err
    except config_entry_oauth2_flow.ImplementationUnavailableError as err:
        raise ConfigEntryNotReady('Lightroom application credentials are not available yet') from err
    manager = LightroomManager(hass, entry, config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation))
    await manager.async_load()
    entry.runtime_data = manager
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await manager.async_close()
        raise
    _register_services(hass)
    hass.data[SERVICE_USERS] = hass.data.get(SERVICE_USERS, 0) + 1
    entry.async_on_unload(entry.add_update_listener(_options_changed))
    manager.start()
    return True


async def async_unload_entry(hass, entry) -> bool:
    if manager := getattr(entry, 'runtime_data', None):
        await manager.async_close()
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    hass.data[SERVICE_USERS] = users = max(hass.data.get(SERVICE_USERS, 1) - 1, 0)
    if users == 0:  # the last Lightroom entry: its actions go; album actions keep their own lifecycle
        for name in SERVICES:
            hass.services.async_remove(DOMAIN, name)
    return True


async def async_remove_entry(hass, entry) -> None:
    """The catalog cache goes with the entry (the token is in the entry data). Photo folders stay."""
    await Store(hass, 1, store_key(entry.entry_id)).async_remove()
    for key in ('awaiting_confirmation', 'path_collision', 'ambiguous_folders', 'signin_expiring'):
        ir.async_delete_issue(hass, DOMAIN, f'lightroom_{entry.entry_id}_{key}')


def _loaded(hass) -> list:
    """Loaded Lightroom entries with their manager."""
    return [e for e in hass.config_entries.async_loaded_entries(DOMAIN)
            if e.data.get('kind') == KIND and isinstance(getattr(e, 'runtime_data', None), LightroomManager)]


def _managers(hass, entry_id: str | None) -> list[LightroomManager]:
    entries = _loaded(hass)
    if entry_id:
        entries = [e for e in entries if e.entry_id == entry_id]
        if not entries:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key='unknown_library')
    return [e.runtime_data for e in entries]


SYNC_SCHEMA = vol.Schema({vol.Optional('entry_id'): cv.string, vol.Optional('confirm', default=False): cv.boolean})
PREVIEW_SCHEMA = vol.Schema({vol.Optional('entry_id'): cv.string})


def _register_services(hass) -> None:
    if hass.services.has_service(DOMAIN, 'lightroom_sync'):
        return

    async def lightroom_sync(call):
        managers = _managers(hass, call.data.get('entry_id') or None)
        return {'libraries': list(await asyncio.gather(*(m.sync(call.data['confirm']) for m in managers)))}

    async def lightroom_preview(call):
        managers = _managers(hass, call.data.get('entry_id') or None)
        return {'libraries': [await m.preview() for m in managers]}

    for name, handler, schema in (('lightroom_sync', lightroom_sync, SYNC_SCHEMA),
                                  ('lightroom_preview', lightroom_preview, PREVIEW_SCHEMA)):
        async_register_admin_service(hass, DOMAIN, name, handler, schema=schema,
                                     supports_response=SupportsResponse.OPTIONAL)
