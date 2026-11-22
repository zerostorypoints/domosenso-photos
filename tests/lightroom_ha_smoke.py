"""Isolated HA smoke for the Lightroom source against a fake Adobe IMS + Lightroom on 127.0.0.1.

Runs inside the official HA image (see README.md, Development). All data is invented: 30 assets in
three albums (two of them inside a set) and two unfiled, with varied ratings, flags, dates and
cameras. The fake Lightroom answers 405 to anything but GET, and the smoke ends by checking that it
never got one. A small fake Synology share runs on the same server so an albums entry can sync
alongside. Set LIGHTROOM_SMOKE_DEBUG=1 to keep Home Assistant's log output.
"""
import asyncio, base64, hashlib, importlib, io, json, logging, os, shutil, time, traceback
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from aiohttp import web
from PIL import Image

from homeassistant import bootstrap, loader
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_entry_oauth2_flow, issue_registry as ir
from homeassistant.helpers import frame as ha_frame
from homeassistant.setup import async_setup_component

if not os.environ.get('LIGHTROOM_SMOKE_DEBUG'):
    logging.disable(logging.CRITICAL)

# HA's usage reports (e.g. closing an HA-managed aiohttp session) are only logged; record them.
REPORTS = []
_report_usage = ha_frame.report_usage


def _record_usage(what, *args, **kwargs):
    REPORTS.append(str(what))
    return _report_usage(what, *args, **kwargs)


ha_frame.report_usage = _record_usage

DOMAIN = 'domosenso_photos'
CLIENT_ID, CLIENT_SECRET = 'fake-client-id-7', 'fake-client-secret-7'
JSON_PREFIX = 'while (1) {}\n'
EXIF_IFD, GPS_IFD = 0x8769, 0x8825
SET, LAKE, PEAKS, GARDEN = 'set-faraway', 'alb-zebra', 'alb-quartz', 'alb-mossy'
NAMES = {SET: 'Faraway Trips', LAKE: 'Zebra Lake', PEAKS: 'Quartz Peaks', GARDEN: 'Mossy Garden'}
# Where each album lands: its path in the album tree (two albums inside the set).
DIR = {LAKE: f'{NAMES[SET]}/{NAMES[LAKE]}', PEAKS: f'{NAMES[SET]}/{NAMES[PEAKS]}', GARDEN: NAMES[GARDEN]}
PAGE = 20  # the fake's page size: the asset list comes in two pages
SHARE_PASS = 'SmokeShare9'


def check(cond, message):
    if not cond:
        raise AssertionError(message)


def b64(obj):
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip('=')


def jwt(claims):
    return f'h.{b64(claims)}.s'


def refresh_jwt(days):
    return jwt({'created_at': str(int(time.time() * 1000)), 'expires_in': str(days * 86400 * 1000)})


PRIVATE_TAGS = {0xA431: 'BODY-0001', 0xA435: 'LENS-0002', 0x927C: b'maker-note'}  # serials, MakerNote
FULLSIZE = 'as00'  # the one asset with a full-size rendition: it kept the original's private tags


def owner(account):
    return 'lightroom:' + hashlib.sha256(account.encode()).hexdigest()[:16]


def jpeg(color, gps=True, private=False):
    """A rendition as the fake serves it; with GPS (and the private tags) so the smoke can show they are stripped."""
    exif = Image.Exif()
    if gps:
        g = exif.get_ifd(GPS_IFD)
        g[1], g[2], g[3], g[4] = 'N', (46.0, 1.0, 0.0), 'E', (7.0, 2.0, 0.0)
    if private:
        exif.get_ifd(EXIF_IFD).update(PRIVATE_TAGS)
    out = io.BytesIO()
    Image.new('RGB', (64, 48), color).save(out, format='JPEG', exif=exif.tobytes())
    return out.getvalue()


def make_asset(n):
    """Invented asset n: rating, flag, edit state, album, capture time and camera vary with n."""
    aid = f'as{n:02}'
    rating = (5, 4, 3, 5, 4, 3, 1, 5, 0, 4)[n % 10]
    if n == 28:
        rating = 4
    flag = 'reject' if n in (2, 13) else 'pick' if n in (0, 7, 20) else None
    capture = f'2025-{6 + n % 3:02}-{1 + n % 28:02}T09:30:00'
    if n == 29:
        capture = '2024-12-24T18:05:00.250+01:00'
    payload = {'captureDate': capture,
               'ratings': {'app': {'rating': rating, 'date': '2025-09-01T00:00:00Z'}},
               'xmp': {'tiff': {'Make': 'Camco' if n % 2 else 'Optix', 'Model': f'Z{n % 4}'},
                       'exif': {'FNumber': [28, 10], 'ISOSpeedRatings': 100 * (1 + n % 4)},
                       'aux': {'Lens': 'Fictar 35mm'} if n % 3 == 0 else {}}}
    if flag:
        payload['reviews'] = {'app': {'flag': flag, 'date': '2025-09-01T00:00:00Z'}}
    if n % 5 != 1:  # every fifth photo is unedited
        payload['develop'] = {'userUpdated': '2025-09-02T00:00:00Z'}
    return {'id': aid, 'subtype': 'image', 'updated': '2025-09-03T00:00:00Z', 'payload': payload}


class FakeAdobe:
    """IMS token endpoint and the Lightroom GET endpoints the client uses."""

    def __init__(self):
        self.base = ''
        self.non_get = 0
        self.account = 'acct-one'
        self.token_status = 200
        self.token_grants = []
        self.delay = 0.0
        self.assets = [make_asset(n) for n in range(30)]
        self.albums = [{'id': SET, 'subtype': 'collection_set', 'payload': {'name': NAMES[SET]}},
                       {'id': LAKE, 'subtype': 'collection', 'payload': {'name': NAMES[LAKE], 'parent': {'id': SET}}},
                       {'id': PEAKS, 'subtype': 'collection', 'payload': {'name': NAMES[PEAKS], 'parent': {'id': SET}}},
                       {'id': GARDEN, 'subtype': 'collection', 'payload': {'name': NAMES[GARDEN]}}]
        self.members = {LAKE: [f'as{n:02}' for n in range(10)], PEAKS: [f'as{n:02}' for n in range(10, 20)],
                        GARDEN: [f'as{n:02}' for n in range(20, 28)]}
        self.missing = set()  # assets without a rendition
        self.colors = {}  # asset id -> rendition colour
        self.renditions = []  # (asset id, status) of every rendition request

    def token(self):
        return {'access_token': jwt({'user_id': self.account, 'n': time.time()}), 'token_type': 'bearer',
                'expires_in': 86400, 'refresh_token': refresh_jwt(14), 'id_token': jwt({'sub': self.account})}

    async def ims(self, request):
        form = dict(await request.post())
        self.token_grants.append(form.get('grant_type'))
        if self.token_status != 200:
            return web.json_response({'error': 'invalid_grant'}, status=self.token_status)
        return web.json_response(self.token())

    def json(self, body):
        return web.Response(text=JSON_PREFIX + json.dumps(body), content_type='application/json')

    async def lightroom(self, request):
        if request.method != 'GET':
            self.non_get += 1
            return web.Response(status=405)
        if request.headers.get('X-API-Key') != CLIENT_ID or not request.headers.get('Authorization', '').startswith('Bearer '):
            return web.Response(status=401)
        if self.delay:
            await asyncio.sleep(self.delay)
        parts = request.path.split('/')
        if request.path == '/v2/catalog':
            return self.json({'id': 'cat1'})
        if request.path == '/v2/catalogs/cat1/albums':
            return self.json({'base': f'{self.base}/v2/catalogs/cat1/', 'resources': self.albums})
        if len(parts) == 7 and parts[4] == 'albums' and parts[6] == 'assets':
            return self.json({'resources': [{'asset': {'id': i}} for i in self.members.get(parts[5], [])]})
        if request.path == '/v2/catalogs/cat1/assets':
            page = int(request.query.get('page', '1'))
            body = {'base': f'{self.base}/v2/catalogs/cat1/', 'resources': self.assets[(page - 1) * PAGE:page * PAGE]}
            if page * PAGE < len(self.assets):
                body['links'] = {'next': {'href': f'assets?page={page + 1}'}}
            return self.json(body)
        if len(parts) == 8 and parts[6] == 'renditions':
            aid = parts[5]
            full = parts[7] == 'fullsize'
            if aid in self.missing or (full and aid != FULLSIZE):  # no full-size rendition was ever generated
                self.renditions.append((aid, 404))
                return web.Response(status=404)
            self.renditions.append((aid, 200))
            return web.Response(body=jpeg(self.colors.get(aid, (20, 120, 40)), private=full), content_type='image/jpeg')
        return web.Response(status=404)

    def fetched(self):
        return sorted(a for a, status in self.renditions if status == 200)


class FakeShare:
    """Enough of DSM's sharing web API for one public share with two JPEGs."""

    def __init__(self):
        self.items = {301: ('IMG_0301.JPG', jpeg((200, 10, 10), gps=False)),
                      302: ('IMG_0302.JPG', jpeg((10, 10, 200), gps=False))}

    async def handle(self, request):
        args = dict(await request.post()) if request.method == 'POST' else dict(request.query)
        api = args.get('api')
        if api == 'SYNO.Core.Sharing.Login':
            resp = web.json_response({'data': {'sharing_sid': 'sid1'}, 'success': True})
            resp.set_cookie('sharing_sid', 'sid1')
            return resp
        if api == 'SYNO.Foto.Browse.Album':
            return web.json_response({'data': {'list': [{'id': 7, 'name': 'Smoke Album', 'item_count': len(self.items),
                                                         'start_time': 1781524800, 'version': 1}]}, 'success': True})
        if api == 'SYNO.Foto.Browse.Item':
            rows = [{'id': i, 'filename': name, 'filesize': len(data), 'time': 1781524800 + i, 'type': 'photo',
                     'additional': {'thumbnail': {'cache_key': f'{i}_1'}}} for i, (name, data) in sorted(self.items.items())]
            offset, limit = int(args['offset']), int(args['limit'])
            return web.json_response({'data': {'list': rows[offset:offset + limit]}, 'success': True})
        if api == 'SYNO.Foto.Download':
            return web.Response(body=self.items[json.loads(args['unit_id'])[0]][1], content_type='application/force-download')
        return web.json_response({'error': {'code': 103}, 'success': False})


# What the default filter (3 stars and up, not rejected) wants, worked out from the fake's data alone.
def wanted(assets, members, ratings=(3, 4, 5), edited_only=False, exclude=(), collided=('alb-quartz',)):
    album_of = {a: alb for alb, ids in members.items() for a in ids}
    out = {}
    for a in assets:
        p = a['payload']
        if p['ratings']['app']['rating'] not in ratings or (p.get('reviews') or {}).get('app', {}).get('flag') == 'reject':
            continue
        if edited_only and 'develop' not in p:
            continue
        alb = album_of.get(a['id'])
        if alb in exclude:
            continue
        if alb:
            folder = f'lr/{DIR[alb]}' + ('-2' if alb in collided else '')
        else:
            folder = 'lr/' + p['captureDate'][:7]
        out.setdefault(folder, set()).add(f"{p['captureDate'][:10]}_{a['id']}.jpg")
    return out


def files(root, prefix='lr'):
    """folder -> photo file names, for the folders that carry a sync marker."""
    base = Path(root, prefix)
    out = {}
    for marker in sorted(base.rglob('.sync.json')):
        names = {p.name for p in marker.parent.iterdir() if p.name != '.sync.json'}
        rel = marker.parent.relative_to(base).as_posix()
        out[f'{prefix}/{rel}' if prefix else rel] = names
    return out


def tree(root):
    """Relative path -> (size, mtime, bytes) of every file under root."""
    root = Path(root)
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime, p.read_bytes())
            for p in sorted(root.rglob('*')) if p.is_file()}


def photo_bytes(root):
    return {k: v[2] for k, v in tree(root).items() if not k.endswith('.sync.json')}


async def wait_for(predicate, message, timeout=30):
    for _ in range(int(timeout / 0.1)):
        if predicate():
            return
        await asyncio.sleep(0.1)
    raise AssertionError(message)


async def main():
    config_dir = '/config'
    fake, share = FakeAdobe(), FakeShare()
    app = web.Application()
    app.router.add_route('POST', '/ims/token/v3', fake.ims)
    app.router.add_route('*', '/{tail:v2/.*}', fake.lightroom)
    app.router.add_route('*', '/photo/mo/sharing/webapi/entry.cgi', share.handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    port = runner.addresses[0][1]
    fake.base = base = f'http://127.0.0.1:{port}'

    hass = HomeAssistant(config_dir)
    loader.async_setup(hass)
    try:
        check(await bootstrap.async_from_config_dict({'http': {'server_host': '127.0.0.1', 'server_port': 8123}}, hass),
              'HA bootstrap failed')
        hass.config.components.add('my')  # the redirect URL is my.home-assistant.io, as on a real install
        creds = importlib.import_module(f'custom_components.{DOMAIN}.application_credentials')
        creds.AUTHORIZE_URL, creds.TOKEN_URL = f'{base}/ims/authorize/v2', f'{base}/ims/token/v3'
        lr_api = importlib.import_module(f'custom_components.{DOMAIN}.lightroom_api')
        lr_api.API_BASE, lr_api.PAUSE = base, 0
        lr = importlib.import_module(f'custom_components.{DOMAIN}.lightroom')
        lr.STARTUP_DELAY_S = 3600  # syncs are started by the smoke (or by Save)
        albums = importlib.import_module(f'custom_components.{DOMAIN}.albums')
        albums.STARTUP_DELAY_S = 3600
        check(await async_setup_component(hass, 'application_credentials', {}), 'application_credentials setup failed')
        check(await async_setup_component(hass, 'diagnostics', {}), 'diagnostics setup failed')
        from homeassistant.components import diagnostics as ha_diag
        from homeassistant.components.application_credentials import ClientCredential, async_import_client_credential
        await async_import_client_credential(hass, DOMAIN, ClientCredential(CLIENT_ID, CLIENT_SECRET), DOMAIN)
        flows = hass.config_entries.flow

        photo_dir = Path(config_dir, 'www', 'ramka')
        foreign = photo_dir / 'lr' / DIR[PEAKS]  # someone else's folder: the album lands at "-2"
        foreign.mkdir(parents=True)
        (foreign / 'notes.txt').write_text('not ours')
        foreign_before = tree(foreign)
        album_dir = Path(config_dir, 'www', 'albums')
        album_dir.mkdir(parents=True)

        def issues():
            return {i.issue_id.removeprefix(f'lightroom_{i.issue_id.split("_")[1]}_'): i
                    for i in ir.async_get(hass).issues.values() if i.domain == DOMAIN and i.issue_id.startswith('lightroom_')}

        async def download(entry):
            """The diagnostics file exactly as HA's download view builds it."""
            info = hass.data[ha_diag._DIAGNOSTICS_DATA].platforms[DOMAIN]
            data = await info.config_entry_diagnostics(hass, entry)
            data_issues = [i.to_json() for k, i in ir.async_get(hass).issues.items() if k[0] == DOMAIN]
            resp = await ha_diag._async_get_json_file_response(hass, data, data_issues, 'f', DOMAIN, entry.entry_id)
            check(resp.status == 200, f'diagnostics download {resp.status}')
            return resp.body.decode()

        def no_secret(text, entry, what):
            for bad in ('lr/', *NAMES.values(), CLIENT_SECRET, CLIENT_ID, 'access_token', 'refresh_token',
                        entry.data['token']['access_token'], entry.data['token']['refresh_token'], 'as03', '.jpg'):
                check(bad not in text, f'{what} contains {bad!r}')

        async def oauth(flow):
            """From the lightroom choice to the step after the token exchange."""
            if flow['type'] == 'form' and flow['step_id'] == 'pick_implementation':
                flow = await flows.async_configure(flow['flow_id'], {'implementation': DOMAIN})
            check(flow['type'] == 'external' and flow['step_id'] == 'auth', f'no external sign-in step: {flow}')
            url = urlsplit(flow['url'])
            q = parse_qs(url.query)
            check(f'{url.scheme}://{url.netloc}{url.path}' == creds.AUTHORIZE_URL and q['client_id'] == [CLIENT_ID],
                  f'authorize URL {flow["url"]}')
            check(q['scope'] == [creds.SCOPES], f'scope {q.get("scope")}')
            # What HA's callback view does with the redirect: decode the state, hand over the code.
            state = config_entry_oauth2_flow._decode_jwt(hass, q['state'][0])
            done = await flows.async_configure(flow['flow_id'], {'state': state, 'code': 'c1'})
            check(done['type'] == 'external_done', f'external step not done: {done}')
            return await flows.async_configure(flow['flow_id'])

        async def start_lightroom():
            flow = await flows.async_init(DOMAIN, context={'source': 'user'})
            check(flow['type'] == 'menu' and set(flow['menu_options']) == {'albums', 'lightroom'}, f'kind menu {flow}')
            return await oauth(await flows.async_configure(flow['flow_id'], {'next_step_id': 'lightroom'}))

        async def call(service, **data):
            return await hass.services.async_call(DOMAIN, service, data, blocking=True, return_response=True)

        async def synced(entry, old, predicate, message):
            """After a reload (Save, an options change): the new manager's immediate sync has finished."""
            def done():
                m = getattr(entry, 'runtime_data', None)  # unset between the unload and the new setup
                return m is not None and m is not old and not m.lock.locked() and predicate(m)

            await wait_for(done, message)
            await hass.async_block_till_done()
            return entry.runtime_data

        async def filter_flow(entry, values):
            o = await hass.config_entries.options.async_init(entry.entry_id)
            check(o['type'] == 'menu' and o['step_id'] == 'lightroom'
                  and list(o['menu_options']) == ['sync_now', 'filter', 'settings'], f'options menu {o}')
            o = await hass.config_entries.options.async_configure(o['flow_id'], {'next_step_id': 'filter'})
            check(o['type'] == 'form' and o['step_id'] == 'filter', f'filter form {o}')
            return await hass.config_entries.options.async_configure(o['flow_id'], values)

        default = {'ratings': ['3', '4', '5'], 'flag': 'not_rejected', 'include_unfiled': True, 'edited_only': False}

        # --- An albums entry, loaded alongside the Lightroom ones for the whole run.
        flow = await flows.async_init(DOMAIN, context={'source': 'user'})
        flow = await flows.async_configure(flow['flow_id'], {'next_step_id': 'albums'})
        made = await flows.async_configure(flow['flow_id'], {'photo_dir': str(album_dir)})
        check(made['type'] == 'create_entry', f'albums entry {made}')
        albums_entry = made['result']
        await hass.async_block_till_done()
        hass.config_entries.async_update_entry(albums_entry, options={
            'interval_min': 60, 'max_edge': 2560, 'quality': 85, 'free_floor': 100_000_000,
            'host_map': [{'from': 'http://photos.invalid', 'to': base}]})
        await wait_for(lambda: albums_entry.state.value == 'loaded' and hass.data.get(albums.DATA) is not None
                       and hass.data[albums.DATA].host_map, 'albums entry did not reload with the host map')
        added = await call('album_add', url=f'http://photos.invalid/photo/mo/sharing/{SHARE_PASS}')
        check(added['album']['status'] == 'ok' and added['album']['photos'] == 2, f'albums sync {added}')

        # --- Config flow: menu -> lightroom -> OAuth -> folder step -> entry.
        folder = await start_lightroom()
        check(folder['type'] == 'form' and folder['step_id'] == 'lightroom_folder', f'folder step {folder}')
        check(fake.token_grants == ['authorization_code'], f'token grants {fake.token_grants}')
        bad = await flows.async_configure(folder['flow_id'], {'photo_dir': str(photo_dir), 'prefix': '../lr'})
        check(bad['type'] == 'form' and bad['errors'] == {'prefix': 'invalid_prefix'}, f'bad prefix accepted {bad}')
        made = await flows.async_configure(folder['flow_id'], {'photo_dir': str(photo_dir), 'prefix': 'lr'})
        check(made['type'] == 'create_entry', f'entry not created {made}')
        entry = made['result']
        await hass.async_block_till_done()
        check(entry.state.value == 'loaded' and entry.unique_id == 'acct-one' and entry.title == 'Lightroom', 'entry not loaded')
        check(entry.data['kind'] == 'lightroom' and entry.data['photo_dir'] == str(photo_dir) and entry.data['prefix'] == 'lr'
              and entry.data['auth_implementation'] == DOMAIN and entry.data['token']['refresh_token'], f'entry data {list(entry.data)}')
        check(dict(entry.options) == lr.DEFAULT_OPTIONS, f'default options {entry.options}')
        check(hass.services.has_service(DOMAIN, 'lightroom_sync') and hass.services.has_service(DOMAIN, 'lightroom_preview'),
              'Lightroom actions not registered')
        store_path = Path(hass.config.path('.storage', lr.store_key(entry.entry_id)))

        # --- A second entry for the same account aborts.
        again = await start_lightroom()
        check(again['type'] == 'abort' and again['reason'] == 'already_configured', f'second entry not aborted {again}')

        # --- First sync: exactly the default-filter photos, in album / month folders, marked, EXIF, no GPS.
        from homeassistant.helpers import entity_registry as er
        from homeassistant.helpers.event import async_track_state_change_event
        ent_reg = er.async_get(hass)
        lr_sensor = ent_reg.async_get_entity_id('sensor', DOMAIN, f'{entry.entry_id}_lightroom')
        check(lr_sensor and not ent_reg.async_get_entity_id('sensor', DOMAIN, f'{entry.entry_id}_lightroom_progress'),
              'one Lightroom sensor, no separate progress sensor')
        check(hass.states.get(lr_sensor).attributes.get('progress') is None, f'progress before a sync {hass.states.get(lr_sensor)}')
        seen = []
        stop_seen = async_track_state_change_event(
            hass, [lr_sensor], lambda ev: seen.append(dict(ev.data['new_state'].attributes)))
        resp = await call('lightroom_sync', entry_id=entry.entry_id)
        await hass.async_block_till_done()
        stop_seen()
        phases = [a.get('phase') for a in seen]
        check('reading_library' in phases and any(a.get('phase') == 'downloading' and a.get('total', 0) > 0 for a in seen),
              f'progress phases {phases}')
        final = hass.states.get(lr_sensor).attributes
        check(final.get('phase') is None and final.get('done') is None and final.get('total') is None, f'progress after {final}')
        check(final.get('progress') == 100 and entry.runtime_data.data.get('library_pages', 0) > 0, f'progress after a sync {final}')
        (lib,) = resp['libraries']
        expected = wanted(fake.assets, fake.members)
        count = sum(map(len, expected.values()))
        check(count == 23 and lib['status'] == 'ok' and lib['added'] == count and lib['skipped_count'] == 0, f'first sync {lib}')
        check(files(photo_dir) == expected, f'files {files(photo_dir)} != {expected}')
        check(set(expected) == {f'lr/{DIR[LAKE]}', f'lr/{DIR[PEAKS]}-2', f'lr/{DIR[GARDEN]}', 'lr/2025-07', 'lr/2024-12'},
              f'folders {set(expected)}')
        check(tree(foreign) == foreign_before, 'the foreign folder was touched')
        by_id = {a['id']: a for a in fake.assets}
        album_key = {f'lr/{DIR[LAKE]}': LAKE, f'lr/{DIR[PEAKS]}-2': PEAKS, f'lr/{DIR[GARDEN]}': GARDEN}
        for path, names in expected.items():
            marker = json.loads((photo_dir / path / '.sync.json').read_text())
            check(marker['owner'] == owner('acct-one') and marker['kind'] == 'lightroom' and marker['path'] == path
                  and marker['key'] == album_key.get(path, path.removeprefix('lr/'))
                  and {v['name'] for v in marker['items'].values()} == names, f'marker {path}: {marker}')
            for name in names:
                asset = by_id[name[11:-4]]
                with Image.open(photo_dir / path / name) as img:
                    exif = img.getexif()
                    check(exif.get(271) == asset['payload']['xmp']['tiff']['Make'] and exif.get(272) == asset['payload']['xmp']['tiff']['Model'],
                          f'camera EXIF {path}/{name}')
                    taken = asset['payload']['captureDate'][:19].replace('-', ':').replace('T', ' ')
                    check(exif.get_ifd(EXIF_IFD).get(36867) == taken, f'DateTimeOriginal {path}/{name}')
                    check(not exif.get_ifd(GPS_IFD), f'GPS kept in {path}/{name}')
                    check(not set(PRIVATE_TAGS) & set(exif.get_ifd(EXIF_IFD)), f'serial or MakerNote kept in {path}/{name}')
        check(sorted(fake.fetched()) == sorted(n[11:-4] for ns in expected.values() for n in ns), 'renditions fetched')
        check((FULLSIZE, 200) in fake.renditions and any(n[11:-4] == FULLSIZE for ns in expected.values() for n in ns),
              'the full-size rendition was not used')
        await hass.async_block_till_done()
        state = hass.states.get('sensor.domosenso_photos_lightroom')
        check(state is not None and state.state == 'ok' and state.attributes['photos'] == count, f'sensor {state}')
        check(state.attributes['path_collisions'] == [f'lr/{DIR[PEAKS]}-2'] and state.attributes['ambiguous_folders'] == [],
              f'sensor attributes {state.attributes}')
        check(issues()['path_collision'].translation_placeholders == {
            'title': 'Lightroom', 'count': '1', 'entity': 'sensor.domosenso_photos_lightroom'}, f'collision issue {issues()}')

        # --- Preview action and diagnostics (coverage; no token, path or name, also with the collision issue on).
        prev = (await call('lightroom_preview', entry_id=entry.entry_id))['libraries'][0]
        check(prev['kept'] == count and prev['add'] == 0 and prev['remove'] == 0 and prev['hold'] is None and prev['cache_date'],
              f'preview {prev}')
        try:
            await call('lightroom_preview', entry_id='no-such-entry')
            check(False, 'unknown entry accepted')
        except ServiceValidationError as err:
            check(err.translation_key == 'unknown_library', f'unknown entry {err.translation_key}')
        d = await lr_diag(hass, entry)
        check(d['kind'] == 'lightroom' and d['asset_count'] == 30 and d['albums'] == 3 and d['sets'] == 1
              and d['coverage']['payload.ratings'] == 30 and d['coverage']['payload.reviews'] == 5
              and d['coverage']['payload.develop.userUpdated'] == 24 and d['coverage']['payload.xmp.aux.Lens'] == 10, f'diagnostics {d}')
        dl = await download(entry)
        check('lightroom_' in dl and 'path_collision' in dl, 'download has no issues')
        no_secret(dl, entry, 'diagnostics download (path collision on)')
        check(CLIENT_SECRET not in store_path.read_text() and entry.data['token']['access_token'] not in store_path.read_text(),
              'store holds a secret')

        # --- edited_only + an excluded album through the options flow: preview counts, Save, files follow.
        target = wanted(fake.assets, fake.members, edited_only=True, exclude=(GARDEN,))
        keep = sum(map(len, target.values()))
        check(0 < count - keep <= count / 2, f'smoke data: this filter must remove some but not more than half ({count - keep})')
        r = await filter_flow(entry, {**default, 'edited_only': True, 'albums_exclude': [GARDEN]})
        check(r['type'] == 'menu' and r['step_id'] == 'filter_preview', f'filter preview {r}')
        ph = r['description_placeholders']
        check(ph['kept'] == str(keep) and ph['add'] == '0' and ph['remove'] == str(count - keep) and ph['move'] == '0'
              and ph['hold'] == '-' and f'lr/{DIR[GARDEN]}' in ph['top'], f'preview placeholders {ph}')
        old = entry.runtime_data
        r = await hass.config_entries.options.async_configure(r['flow_id'], {'next_step_id': 'save'})
        check(r['type'] == 'create_entry' and entry.options['edited_only'] and entry.options['albums_exclude'] == [GARDEN], f'save {r}')
        m = await synced(entry, old, lambda m: files(photo_dir) == target, f'files after Save {files(photo_dir)}')
        check(m.data['status'] == 'ok' and m.data['counts']['removed'] == count - keep, f'after Save {m.data.get("counts")}')
        check(old.http.closed, 'the old manager left its session open')

        # --- Back to the default filter (adds only), then a filter removing more than half: held.
        old = entry.runtime_data
        hass.config_entries.async_update_entry(entry, options={**entry.options, 'edited_only': False, 'albums_exclude': []})
        m = await synced(entry, old, lambda m: files(photo_dir) == expected, 'default filter not restored')
        r = await filter_flow(entry, {**default, 'ratings': ['1']})
        ph = r['description_placeholders']
        check(r['step_id'] == 'filter_preview' and ph['hold'] == 'mass_remove' and ph['remove'] == str(count), f'held preview {ph}')
        old = entry.runtime_data
        r = await hass.config_entries.options.async_configure(r['flow_id'], {'next_step_id': 'save'})
        m = await synced(entry, old, lambda m: m.data.get('status') == 'awaiting_confirmation', 'no hold after Save')
        check(m.data['hold'] == 'mass_remove' and issues()['awaiting_confirmation'].translation_placeholders['reason'] == 'mass_remove',
              f'hold issue {issues()}')
        ones = wanted(fake.assets, fake.members, ratings=(1,))
        check(all(files(photo_dir).get(k, set()) >= v for k, v in expected.items()), 'a held run removed photos')
        resp = await call('lightroom_sync', entry_id=entry.entry_id, confirm=True)
        check(resp['libraries'][0]['status'] == 'ok' and resp['libraries'][0]['removed'] == count, f'confirm {resp}')
        check(files(photo_dir) == ones and 'awaiting_confirmation' not in issues(), f'after confirm {files(photo_dir)}')
        old = entry.runtime_data
        hass.config_entries.async_update_entry(entry, options={**entry.options, 'ratings': [3, 4, 5]})
        m = await synced(entry, old, lambda m: files(photo_dir) == expected and m.data.get('status') == 'ok',
                         'default filter not restored after the hold')

        # --- An edit in Lightroom re-downloads that one photo.
        before = photo_bytes(photo_dir)
        fake.renditions.clear()
        by_id['as03']['payload']['develop']['userUpdated'] = '2025-10-01T08:00:00Z'
        fake.colors['as03'] = (250, 250, 0)
        lib = (await call('lightroom_sync', entry_id=entry.entry_id))['libraries'][0]
        check(lib['status'] == 'ok' and lib['updated'] == 1 and lib['added'] == 0 and fake.fetched() == ['as03'], f'edit {lib} {fake.renditions}')
        after = photo_bytes(photo_dir)
        changed = sorted(k for k in after if after[k] != before.get(k))
        check(changed == [f'lr/{DIR[LAKE]}/2025-06-04_as03.jpg'] and after.keys() == before.keys(), f'edit changed {changed}')

        # --- A rendition that answers 404 is skipped; the sync is still ok.
        fake.assets.append(make_asset(30))
        fake.members[LAKE].append('as30')
        fake.missing = {'as30'}
        lib = (await call('lightroom_sync', entry_id=entry.entry_id))['libraries'][0]
        check(lib['status'] == 'ok' and lib['skipped_count'] == 1 and lib['added'] == 0, f'404 {lib}')
        await hass.async_block_till_done()
        check(hass.states.get('sensor.domosenso_photos_lightroom').attributes['skipped_count'] == 1, 'sensor skipped_count')
        fake.assets.pop()
        fake.members[LAKE].pop()
        fake.missing = set()
        lib = (await call('lightroom_sync', entry_id=entry.entry_id))['libraries'][0]
        check(lib['status'] == 'ok' and lib['skipped_count'] == 0 and files(photo_dir) == expected, f'after 404 {lib}')

        # --- A copied album folder after the original was moved: ambiguous, both left byte-identical.
        lake = photo_dir / 'lr' / DIR[LAKE]
        moved, copy = photo_dir / 'lr' / 'Moved Lake', photo_dir / 'lr' / 'Copied Lake'
        lake.rename(moved)
        shutil.copytree(moved, copy)
        moved_before, copy_before = tree(moved), tree(copy)
        lib = (await call('lightroom_sync', entry_id=entry.entry_id))['libraries'][0]
        check(lib['status'] == 'ok' and issues()['ambiguous_folders'].translation_placeholders == {
            'title': 'Lightroom', 'count': '2', 'entity': 'sensor.domosenso_photos_lightroom'}, f'ambiguous issue {lib} {issues()}')
        check(tree(moved) == moved_before and tree(copy) == copy_before and not lake.exists(), 'an ambiguous folder was touched')
        await hass.async_block_till_done()
        check(hass.states.get('sensor.domosenso_photos_lightroom').attributes['ambiguous_folders'] == ['lr/Copied Lake', 'lr/Moved Lake'],
              'sensor ambiguous_folders')
        dl = await download(entry)
        check('ambiguous_folders' in dl and 'path_collision' in dl, 'download lacks the path issues')
        no_secret(dl, entry, 'diagnostics download (ambiguous folders on)')
        shutil.rmtree(copy)
        lib = (await call('lightroom_sync', entry_id=entry.entry_id))['libraries'][0]
        check('ambiguous_folders' not in issues() and files(photo_dir) == expected and not moved.exists(),
              f'moved folder not adopted back {files(photo_dir)}')

        # --- Reading the library reports its pages against the last read's count.
        lr.PROGRESS_EVERY_S = 0
        pages_before = entry.runtime_data.data['library_pages']
        seen = []
        stop_seen = async_track_state_change_event(
            hass, [lr_sensor], lambda ev: seen.append(dict(ev.data['new_state'].attributes)))
        await call('lightroom_sync', entry_id=entry.entry_id)
        await hass.async_block_till_done()
        stop_seen()
        lr.PROGRESS_EVERY_S = 5
        reading = [a for a in seen if a.get('phase') == 'reading_library']
        check(reading and reading[-1]['done'] == pages_before and reading[-1]['total'] == pages_before
              and reading[-1]['progress'] == 99 and any(0 < a['progress'] < 99 for a in reading if a['progress'] is not None),
              f'reading progress {[(a["done"], a["total"], a["progress"]) for a in reading]}')

        # --- A set renamed in Lightroom: its albums' folders move, nothing is downloaded; the old set
        # folder stays while it holds someone else's folder.
        fake.albums[0]['payload']['name'] = 'Far Trips'
        fake.renditions.clear()
        lib = (await call('lightroom_sync', entry_id=entry.entry_id))['libraries'][0]
        renamed = {k.replace(f'lr/{NAMES[SET]}/', 'lr/Far Trips/').replace(f'{NAMES[PEAKS]}-2', NAMES[PEAKS]): v
                   for k, v in expected.items()}
        check(lib['status'] == 'ok' and lib['added'] == 0 and not fake.fetched() and files(photo_dir) == renamed,
              f'set rename {lib} {files(photo_dir)}')
        check(tree(foreign) == foreign_before and sorted(p.name for p in (photo_dir / 'lr' / NAMES[SET]).iterdir()) == [NAMES[PEAKS]],
              'the old set folder lost its foreign content or kept ours')
        fake.albums[0]['payload']['name'] = NAMES[SET]
        lib = (await call('lightroom_sync', entry_id=entry.entry_id))['libraries'][0]
        check(lib['added'] == 0 and files(photo_dir) == expected and not (photo_dir / 'lr' / 'Far Trips').exists(),
              f'set renamed back {lib} {files(photo_dir)}')

        # --- Settings: the folder can change after a confirmation, also during a sync (which stops);
        # the old folder is left as it is.
        async def settings_flow(values):
            o = await hass.config_entries.options.async_init(entry.entry_id)
            o = await hass.config_entries.options.async_configure(o['flow_id'], {'next_step_id': 'settings'})
            check(o['type'] == 'form' and o['step_id'] == 'lightroom_settings', f'settings form {o}')
            return await hass.config_entries.options.async_configure(o['flow_id'], {
                'photo_dir': str(photo_dir), 'prefix': 'lr', 'interval_min': entry.options['interval_min'],
                'max_edge': entry.options['max_edge'], 'quality': entry.options['quality'],
                'free_floor_gb': entry.options['free_floor'] / 1e9, **values})

        old = entry.runtime_data
        r = await settings_flow({})
        check(r['type'] == 'create_entry' and entry.runtime_data is old, f'unchanged settings {r}')
        r = await settings_flow({'photo_dir': str(photo_dir / 'missing')})
        check(r['type'] == 'form' and r['errors'] == {'photo_dir': 'invalid_photo_dir'}, f'missing folder accepted {r}')
        other = Path(config_dir, 'www', 'lr-other')
        other.mkdir()
        before = tree(photo_dir / 'lr')
        fake.delay = 0.5
        manager = entry.runtime_data

        async def background_sync():
            try:
                return await manager.sync()
            except BaseException as err:  # noqa: BLE001 - cancelled by the folder change
                return err

        running = asyncio.ensure_future(background_sync())
        await wait_for(lambda: manager.lock.locked(), 'sync did not start')
        r = await settings_flow({'photo_dir': str(other), 'prefix': '', 'interval_min': 120})
        check(r['type'] == 'form' and r['step_id'] == 'lightroom_folder_confirm'
              and r['description_placeholders'] == {'old': f'{photo_dir}/lr', 'new': str(other)}, f'confirm step {r}')
        check(entry.data['photo_dir'] == str(photo_dir), 'the folder changed before the confirmation')
        r = await hass.config_entries.options.async_configure(r['flow_id'], {})
        check(r['type'] == 'create_entry' and entry.data['photo_dir'] == str(other) and entry.data['prefix'] == ''
              and entry.options['interval_min'] == 120, f'folder change {r} {entry.data.get("photo_dir")}')
        outcome = await running
        check(isinstance(outcome, asyncio.CancelledError), f'the running sync was not stopped: {outcome}')
        fake.delay = 0.0
        in_other = {k.removeprefix('lr/').replace('-2', ''): v for k, v in expected.items()}
        m = await synced(entry, old, lambda m: {k.lstrip('/'): v for k, v in files(other, '').items()} == in_other,
                         f'new folder {files(other, "")}')
        check(m.data['counts']['added'] == sum(map(len, expected.values())) and tree(photo_dir / 'lr') == before, 'old folder touched or not all downloaded')
        old = m
        r = await settings_flow({'interval_min': 360})
        check(r['type'] == 'form' and r['step_id'] == 'lightroom_folder_confirm', f'confirm back {r}')
        r = await hass.config_entries.options.async_configure(r['flow_id'], {})
        m = await synced(entry, old, lambda m: m.data.get('status') == 'ok', 'switch back')
        check(entry.data['photo_dir'] == str(photo_dir) and entry.data['prefix'] == 'lr' and m.data['counts']['added'] == 0
              and files(photo_dir) == expected, f'switch back downloaded again {m.data.get("counts")}')
        shutil.rmtree(other)

        # --- The token endpoint answering 400: auth_failed and a reauth flow.
        token = dict(entry.data['token'])
        hass.config_entries.async_update_entry(entry, data={**entry.data, 'token': {**token, 'expires_at': time.time() - 60}})
        fake.token_status = 400
        lib = (await call('lightroom_sync', entry_id=entry.entry_id))['libraries'][0]
        check(lib['status'] == 'error' and lib['error'] == 'auth_failed' and fake.token_grants[-1] == 'refresh_token', f'400 {lib}')
        reauth = [f for f in flows.async_progress_by_handler(DOMAIN)
                  if f['context'].get('source') == 'reauth' and f['context'].get('entry_id') == entry.entry_id]
        check(len(reauth) == 1 and reauth[0]['step_id'] == 'reauth_confirm', f'reauth flow {reauth}')
        flows.async_abort(reauth[0]['flow_id'])
        fake.token_status = 200
        hass.config_entries.async_update_entry(entry, data={**entry.data, 'token': token})
        check(entry.runtime_data is m, 'a token update reloaded the entry')

        # --- A sign-in ending in 2 days raises signin_expiring.
        hass.config_entries.async_update_entry(entry, data={**entry.data, 'token': {**token, 'refresh_token': refresh_jwt(2)}})
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        check('signin_expiring' in issues(), f'signin issue {issues()}')
        check(entry.runtime_data.data.get('status') == 'error' and entry.runtime_data.data.get('last_success')
              and hass.states.get(lr_sensor).attributes.get('progress') is None,
              f'progress after a reload following a failed run {hass.states.get(lr_sensor)}')
        hass.config_entries.async_update_entry(entry, data={**entry.data, 'token': token})
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        check('signin_expiring' not in issues(), 'signin issue stays')

        # --- Two Lightroom entries (two accounts) at once: both actions work; unloading one keeps them.
        fake.account = 'acct-two'
        folder = await start_lightroom()
        made = await flows.async_configure(folder['flow_id'], {'photo_dir': str(photo_dir), 'prefix': 'lr2'})
        check(made['type'] == 'create_entry', f'second account {made}')
        entry2 = made['result']
        fake.account = 'acct-one'
        await hass.async_block_till_done()
        check(entry2.unique_id == 'acct-two' and entry2.state.value == 'loaded', 'second account not loaded')
        libs = (await call('lightroom_sync'))['libraries']
        check(sorted((x['entry_id'], x['status']) for x in libs) == sorted([(entry.entry_id, 'ok'), (entry2.entry_id, 'ok')])
              and files(photo_dir, 'lr2') == {k.replace('lr/', 'lr2/').replace('-2', ''): v for k, v in expected.items()},
              f'two libraries {libs} {files(photo_dir, "lr2")}')
        check(json.loads((photo_dir / 'lr2' / DIR[LAKE] / '.sync.json').read_text())['owner'] == owner('acct-two'), 'second owner')
        check(files(photo_dir) == expected, 'the second account touched the first one\'s folders')
        prevs = (await call('lightroom_preview'))['libraries']
        check(len(prevs) == 2 and all(p['kept'] == count for p in prevs), f'two previews {prevs}')
        check(await hass.config_entries.async_unload(entry2.entry_id), 'unload second')
        check(hass.services.has_service(DOMAIN, 'lightroom_sync') and hass.services.has_service(DOMAIN, 'lightroom_preview'),
              'actions removed while a Lightroom entry is still loaded')
        libs = (await call('lightroom_sync'))['libraries']
        check([x['entry_id'] for x in libs] == [entry.entry_id] and libs[0]['status'] == 'ok', f'after unloading one {libs}')
        await hass.config_entries.async_remove(entry2.entry_id)
        await hass.async_block_till_done()

        # --- Unload during a running sync cancels it and leaves the store unchanged; the last unload removes the actions.
        stored = store_path.read_bytes()
        fake.delay = 2.0
        manager = entry.runtime_data

        async def background_sync():
            try:
                return await manager.sync()
            except BaseException as err:  # noqa: BLE001 - cancelled by the unload
                return err

        running = asyncio.ensure_future(background_sync())
        await wait_for(lambda: manager.lock.locked(), 'sync did not start')
        check(await hass.config_entries.async_unload(entry.entry_id), 'unload during sync failed')
        outcome = await running
        check(isinstance(outcome, asyncio.CancelledError), f'sync finished instead of being cancelled: {outcome}')
        check(store_path.read_bytes() == stored, 'store changed by a cancelled sync')
        check(not hass.services.has_service(DOMAIN, 'lightroom_sync') and not hass.services.has_service(DOMAIN, 'lightroom_preview'),
              'actions kept after the last Lightroom entry unloaded')
        fake.delay = 0.0
        check(await hass.config_entries.async_setup(entry.entry_id), 'setup after unload failed')
        await hass.async_block_till_done()
        check(hass.services.has_service(DOMAIN, 'lightroom_sync'), 'actions not back after setup')

        # --- Remove the entry: the store goes, the folders stay. Re-adding the account takes them back.
        folders_before = photo_bytes(photo_dir / 'lr')
        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()
        check(not store_path.exists(), 'store kept after removal')
        check(photo_bytes(photo_dir / 'lr') == folders_before and files(photo_dir) == expected, 'removal touched the folders')
        check(not issues(), f'issues after removal {issues()}')
        folder = await start_lightroom()
        made = await flows.async_configure(folder['flow_id'], {'photo_dir': str(photo_dir), 'prefix': 'lr'})
        check(made['type'] == 'create_entry', f're-add {made}')
        entry = made['result']
        await hass.async_block_till_done()
        fake.renditions.clear()
        lib = (await call('lightroom_sync', entry_id=entry.entry_id))['libraries'][0]
        check(lib['status'] == 'ok' and lib['added'] == 0 and lib['moved'] == 0 and lib['removed'] == 0 and not fake.fetched(),
              f're-add synced {lib} {fake.renditions}')
        check(photo_bytes(photo_dir / 'lr') == folders_before and files(photo_dir) == expected, 're-add made new folders')
        check(not any(p.name.endswith('-2') for p in (photo_dir / 'lr').rglob('*')
                      if p.is_dir() and p.relative_to(photo_dir / 'lr').as_posix() != f'{DIR[PEAKS]}-2'), 'a "-2" folder')

        # --- The albums entry still syncs with Lightroom loaded beside it.
        records = (await call('album_sync'))['albums']
        check(len(records) == 1 and records[0]['status'] == 'ok' and records[0]['photos'] == 2, f'albums alongside {records}')
        check(albums_entry.state.value == 'loaded' and entry.state.value == 'loaded', 'both kinds not loaded together')

        check(await hass.config_entries.async_unload(entry.entry_id), 'final unload')
        check(not hass.services.has_service(DOMAIN, 'lightroom_sync') and hass.services.has_service(DOMAIN, 'album_sync'),
              'unloading Lightroom changed the album actions')
        check(not REPORTS, f'HA usage reports: {REPORTS}')
        check(fake.non_get == 0, f'the fake Lightroom got {fake.non_get} non-GET requests')
        print(json.dumps({'config_flow': 'ok', 'sync': 'ok', 'filter': 'ok', 'hold': 'ok', 'edit': 'ok', 'skip': 'ok',
                          'ambiguous': 'ok', 'auth': 'ok', 'two_accounts': 'ok', 'diagnostics': 'ok', 'lifecycle': 'ok',
                          'albums_alongside': 'ok', 'non_get': fake.non_get}))
    finally:
        await hass.async_stop(force=True)
        await runner.cleanup()


async def lr_diag(hass, entry):
    diag = importlib.import_module(f'custom_components.{DOMAIN}.diagnostics')
    return await diag.async_get_config_entry_diagnostics(hass, entry)


if __name__ == '__main__':
    try:
        asyncio.run(main())
        print('lightroom smoke ok')
    except AssertionError as err:
        print(f'lightroom smoke failed: {err}')
        raise SystemExit(1) from None
    except Exception:
        traceback.print_exc()
        print('lightroom smoke failed: unexpected exception (invented test data only)')
        raise SystemExit(1) from None
