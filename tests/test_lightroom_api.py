"""Async Lightroom client: fake transport only, no network, no Home Assistant import."""
import asyncio
import base64
import importlib
import json
import sys
import types
from datetime import UTC, datetime
from pathlib import Path

import pytest

pkg = types.ModuleType('lightroom_api_pkg')
pkg.__path__ = [str(Path(__file__).parents[1] / 'custom_components/domosenso_photos')]
sys.modules[pkg.__name__] = pkg


class _Api:
    def __getattr__(self, name):
        if name.startswith('__') or name == 'pytestmark':
            raise AttributeError(name)
        try:
            return getattr(importlib.import_module('lightroom_api_pkg.lightroom_api'), name)
        except ModuleNotFoundError:
            pytest.fail('lightroom_api module is missing')


api = _Api()
BASE = 'https://lr.adobe.io'
JPEG = b'\xff\xd8\xff\xe0jpegbody'
PREFIX = 'while (1) {}\n'


def jbody(obj, prefix=True):
    return (PREFIX if prefix else '') + json.dumps(obj)


class Fake:
    """(method, url) -> list of responses; the last one repeats. A response is (status, ctype, body[, headers])
    or an exception to raise."""

    def __init__(self, routes):
        self.routes = {k: list(v) for k, v in routes.items()}
        self.calls = []

    async def __call__(self, method, url, params=None, data=None, headers=None):
        self.calls.append((method, url, params, headers))
        queue = self.routes.get((method, url))
        if queue is None:
            raise AssertionError(f'unexpected {method} {url}')
        res = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(res, Exception):
            raise res
        return res


def ok(obj, prefix=True):
    return 200, 'application/json', jbody(obj, prefix).encode()


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    async def sleep(self, secs):
        self.sleeps.append(secs)
        self.now += secs


def make(routes, **kw):
    fake = Fake(routes)
    clk = Clock()

    async def token():
        return 'ACCESS'
    client = api.LightroomClient(fake, 'KEY', token, sleep=clk.sleep, clock=clk.clock, **kw)
    return client, fake, clk


def run(coro):
    return asyncio.run(coro)


def test_headers_carry_api_key_and_bearer():
    client, fake, _ = make({('GET', f'{BASE}/v2/catalog'): [ok({'id': 'C1'})]})
    assert run(client.catalog_id()) == 'C1'
    assert fake.calls[0][3] == {'X-API-Key': 'KEY', 'Authorization': 'Bearer ACCESS'}


def test_prefix_stripped():
    client, _, _ = make({('GET', f'{BASE}/v2/catalog'): [ok({'id': 'C1'}, prefix=True)]})
    assert run(client.catalog_id()) == 'C1'
    client, _, _ = make({('GET', f'{BASE}/v2/catalog'): [ok({'id': 'C2'}, prefix=False)]})
    assert run(client.catalog_id()) == 'C2'


def test_paging_follows_next():
    first = {'base': f'{BASE}/v2/catalogs/C/', 'resources': [{'id': 'a', 'subtype': 'image'}, {'id': 'x', 'subtype': 'video'}],
             'links': {'next': {'href': 'assets?subtype=image&updated_since=T'}}}
    second = {'base': f'{BASE}/v2/catalogs/C/', 'resources': [{'id': 'b', 'subtype': 'image'}]}
    client, fake, _ = make({('GET', f'{BASE}/v2/catalogs/C/assets'): [ok(first)],
                            ('GET', f'{BASE}/v2/catalogs/C/assets?subtype=image&updated_since=T'): [ok(second)]})
    assert [a['id'] for a in run(client.assets('C'))] == ['a', 'b']
    assert fake.calls[0][2] == {'subtype': 'image', 'limit': 100}
    assert fake.calls[1][2] is None


def test_next_href_to_another_host_is_refused():
    page = {'base': 'https://evil.example/', 'resources': [], 'links': {'next': {'href': 'x'}}}
    client, fake, _ = make({('GET', f'{BASE}/v2/catalogs/C/assets'): [ok(page)]})
    with pytest.raises(api.Unreachable):
        run(client.assets('C'))
    assert len(fake.calls) == 1


def test_albums_and_album_asset_ids():
    albums = {'base': f'{BASE}/v2/catalogs/C/', 'resources': [{'id': 'a', 'subtype': 'collection'},
                                                           {'id': 's', 'subtype': 'collection_set'}]}
    members = {'base': f'{BASE}/v2/catalogs/C/', 'resources': [{'asset': {'id': 'p1'}}, {'asset': {'id': 'p2'}}]}
    client, _, _ = make({('GET', f'{BASE}/v2/catalogs/C/albums'): [ok(albums)],
                         ('GET', f'{BASE}/v2/catalogs/C/albums/a/assets'): [ok(members)]})
    assert [a['id'] for a in run(client.albums('C'))] == ['a', 's']
    assert run(client.album_asset_ids('C', 'a')) == ['p1', 'p2']


def test_throttle_waits_between_calls():
    client, _, clk = make({('GET', f'{BASE}/v2/catalog'): [ok({'id': 'C'})]})

    async def two():
        await client.catalog_id()
        await client.catalog_id()
    run(two())
    assert len(clk.sleeps) == 1 and clk.sleeps[0] == pytest.approx(0.2)


def test_retry_after_then_success():
    client, fake, clk = make({('GET', f'{BASE}/v2/catalog'): [
        (429, 'text/plain', b'', {'Retry-After': '7'}), ok({'id': 'C'})]})
    assert run(client.catalog_id()) == 'C'
    assert 7 in clk.sleeps and len(fake.calls) == 2


def test_5xx_three_retries_then_unreachable():
    client, fake, clk = make({('GET', f'{BASE}/v2/catalog'): [(503, 'text/plain', b'')]})
    with pytest.raises(api.Unreachable):
        run(client.catalog_id())
    assert len(fake.calls) == 4
    assert [s for s in clk.sleeps if s >= 1] == [2, 4, 8]


def test_oserror_is_retried_then_unreachable():
    client, fake, _ = make({('GET', f'{BASE}/v2/catalog'): [OSError('boom'), ok({'id': 'C'})]})
    assert run(client.catalog_id()) == 'C'
    client, fake, _ = make({('GET', f'{BASE}/v2/catalog'): [OSError('boom')]})
    with pytest.raises(api.Unreachable):
        run(client.catalog_id())
    assert len(fake.calls) == 4


def test_redirect_is_unreachable_redirect():
    client, fake, _ = make({('GET', f'{BASE}/v2/catalog'): [(302, 'text/html', b'')]})
    with pytest.raises(api.Unreachable) as err:
        run(client.catalog_id())
    assert err.value.code == 'redirect' and len(fake.calls) == 1


def test_listing_other_4xx_is_client_error():
    client, _, _ = make({('GET', f'{BASE}/v2/catalog'): [(400, 'text/plain', b'')]})
    with pytest.raises(api.Unreachable) as err:
        run(client.catalog_id())
    assert err.value.code == 'client_error'


@pytest.mark.parametrize('status', [401, 403])
def test_listing_401_is_auth_refused(status):
    client, _, _ = make({('GET', f'{BASE}/v2/catalog'): [(status, 'text/plain', b'')]})
    with pytest.raises(api.AuthRefused):
        run(client.catalog_id())


def rendition_url(edge):
    return f'{BASE}/v2/catalogs/C/assets/A/renditions/{edge}'


@pytest.mark.parametrize('res', [(403, 'text/plain', b''), (401, 'text/plain', b''), (404, 'text/plain', b''),
                                 (202, 'image/jpeg', JPEG)])
def test_rendition_refusals_are_unavailable_not_auth(res):
    client, _, _ = make({('GET', rendition_url(2048)): [res]})
    with pytest.raises(api.RenditionUnavailable):
        run(client.image('C', 'A', 1024))


def test_rendition_non_jpeg_is_unavailable():
    client, _, _ = make({('GET', rendition_url(2048)): [(200, 'text/html', b'<html>')]})
    with pytest.raises(api.RenditionUnavailable):
        run(client.image('C', 'A', 2048))


def test_image_small_edge_uses_2048():
    client, fake, _ = make({('GET', rendition_url(2048)): [(200, 'image/jpeg', JPEG)]})
    assert run(client.image('C', 'A', 2048)) == JPEG
    assert [c[1] for c in fake.calls] == [rendition_url(2048)]


def test_image_large_edge_uses_existing_fullsize():
    client, fake, _ = make({('GET', rendition_url('fullsize')): [(200, 'image/jpeg', JPEG)]})
    assert run(client.image('C', 'A', 2560)) == JPEG
    assert [c[1] for c in fake.calls] == [rendition_url('fullsize')]


def test_image_fullsize_missing_falls_back_to_2048():
    client, fake, _ = make({('GET', rendition_url('fullsize')): [(404, 'text/plain', b'')],
                            ('GET', rendition_url(2048)): [(200, 'image/jpeg', JPEG)]})
    assert run(client.image('C', 'A', 4000)) == JPEG
    assert [c[1] for c in fake.calls] == [rendition_url('fullsize'), rendition_url(2048)]


@pytest.mark.parametrize('method', ['POST', 'DELETE', 'PUT', 'PATCH', 'get'])
def test_client_refuses_non_get(method):
    client, fake, _ = make({})
    with pytest.raises(ValueError, match='read-only'):
        run(client._request(method, f'{BASE}/v2/catalog'))
    assert fake.calls == []


def test_all_calls_in_a_full_read_are_get():
    page = {'base': f'{BASE}/v2/catalogs/C/', 'resources': [{'id': 'a', 'subtype': 'image'}]}
    albums = {'base': f'{BASE}/v2/catalogs/C/', 'resources': [{'id': 'al', 'subtype': 'collection'}]}
    members = {'base': f'{BASE}/v2/catalogs/C/', 'resources': [{'asset': {'id': 'a'}}]}
    routes = {('GET', f'{BASE}/v2/catalog'): [ok({'id': 'C'})],
              ('GET', f'{BASE}/v2/catalogs/C/assets'): [ok(page)],
              ('GET', f'{BASE}/v2/catalogs/C/albums'): [ok(albums)],
              ('GET', f'{BASE}/v2/catalogs/C/albums/al/assets'): [ok(members)]}
    for n in (1, 2, 3):
        routes[('GET', f'{BASE}/v2/catalogs/C/assets/A{n}/renditions/2048')] = [(200, 'image/jpeg', JPEG)]
    client, fake, _ = make(routes)

    async def read():
        cat = await client.catalog_id()
        await client.assets(cat)
        for al in await client.albums(cat):
            await client.album_asset_ids(cat, al['id'])
        for n in (1, 2, 3):
            await client.image(cat, f'A{n}', 2048)
    run(read())
    assert len(fake.calls) == 7 and {c[0] for c in fake.calls} == {'GET'}


def jwt(claims):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b'=').decode()
    return f'{enc({"alg": "RS256"})}.{enc(claims)}.sig'


def test_account_id_from_claim():
    assert api.account_id({'access_token': jwt({'user_id': 'U'}), 'id_token': jwt({'sub': 'S'})}) == 'S'
    assert api.account_id({'access_token': jwt({'user_id': 'U', 'sub': 'X'})}) == 'U'
    assert api.account_id({'access_token': jwt({'sub': 'X'})}) == 'X'
    assert api.account_id({'access_token': 'garbage'}) is None


def test_signin_expires_from_refresh_jwt():
    token = {'refresh_token': jwt({'created_at': '1700000000000', 'expires_in': '1209600000'})}
    assert api.signin_expires(token) == datetime.fromtimestamp(1700000000 + 1209600, UTC)
    assert api.signin_expires({'refresh_token': jwt({'created_at': 1700000000000, 'expires_in': 1209600000})}).tzinfo


def test_signin_expires_unreadable_is_none():
    assert api.signin_expires({'refresh_token': 'nope'}) is None
    assert api.signin_expires({}) is None
    assert api.signin_expires({'refresh_token': jwt({'created_at': 'x'})}) is None


def test_request_outside_lightroom_is_refused_before_the_token():
    fake = Fake({})
    asked = []

    async def token():
        asked.append(1)
        return 'ACCESS'
    client = api.LightroomClient(fake, 'KEY', token, sleep=Clock().sleep, clock=Clock().clock)
    for url in ('https://evil.example/v2/catalog', 'https://lr.adobe.io.evil.example/v2/catalog', BASE):
        with pytest.raises(api.Unreachable):
            run(client._request('GET', url))
    assert fake.calls == [] and asked == []


def test_retry_after_is_capped():
    client, _, clk = make({('GET', f'{BASE}/v2/catalog'): [
        (429, 'text/plain', b'', {'Retry-After': '86400'}), ok({'id': 'C'})]})
    assert run(client.catalog_id()) == 'C'
    assert max(clk.sleeps) == 300


def test_paging_loop_is_unreachable():
    loop = {'base': f'{BASE}/v2/catalogs/C/', 'resources': [], 'links': {'next': {'href': 'assets?page=2'}}}
    client, fake, _ = make({('GET', f'{BASE}/v2/catalogs/C/assets'): [ok(loop)],
                            ('GET', f'{BASE}/v2/catalogs/C/assets?page=2'): [ok(loop)]})
    with pytest.raises(api.Unreachable):
        run(client.assets('C'))
    assert len(fake.calls) == 2


def test_paging_stops_at_the_page_cap(monkeypatch):
    monkeypatch.setattr(importlib.import_module('lightroom_api_pkg.lightroom_api'), 'MAX_PAGES', 3)
    routes = {}
    for n in range(1, 6):
        url = f'{BASE}/v2/catalogs/C/assets' + ('' if n == 1 else f'?page={n}')
        routes[('GET', url)] = [ok({'base': f'{BASE}/v2/catalogs/C/', 'resources': [],
                                    'links': {'next': {'href': f'assets?page={n + 1}'}}})]
    client, fake, _ = make(routes)
    with pytest.raises(api.Unreachable):
        run(client.assets('C'))
    assert len(fake.calls) == 3


@pytest.mark.parametrize('res', [(503, 'text/plain', b''), (429, 'text/plain', b''), OSError('boom')])
def test_rendition_outage_is_transient_unavailable(res):
    client, fake, _ = make({('GET', rendition_url(2048)): [res]})
    with pytest.raises(api.RenditionUnavailable) as err:
        run(client.image('C', 'A', 2048))
    assert err.value.transient and len(fake.calls) == 4


def test_rendition_too_large_is_unavailable():
    client, _, _ = make({('GET', rendition_url(2048)): [api._TransportRefused('Response too large', 'too_large')]})
    with pytest.raises(api.RenditionUnavailable) as err:
        run(client.image('C', 'A', 2048))
    assert not err.value.transient


def test_listing_too_large_stays_unreachable():
    client, _, _ = make({('GET', f'{BASE}/v2/catalog'): [api._TransportRefused('Response too large', 'too_large')]})
    with pytest.raises(api.Unreachable) as err:
        run(client.catalog_id())
    assert err.value.code == 'too_large'


def test_rendition_404_is_not_transient():
    client, _, _ = make({('GET', rendition_url(2048)): [(404, 'text/plain', b'')]})
    with pytest.raises(api.RenditionUnavailable) as err:
        run(client.image('C', 'A', 2048))
    assert not err.value.transient


@pytest.mark.parametrize('res', [(503, 'text/plain', b''), (302, 'text/html', b''), 'too_large'])
def test_image_any_fullsize_failure_falls_back_to_2048(res):
    if res == 'too_large':
        res = api._TransportRefused('Response too large', 'too_large')
    client, fake, _ = make({('GET', rendition_url('fullsize')): [res],
                            ('GET', rendition_url(2048)): [(200, 'image/jpeg', JPEG)]})
    assert run(client.image('C', 'A', 4000)) == JPEG
    assert fake.calls[-1][1] == rendition_url(2048)


def test_on_page_is_called_for_every_listing_page():
    first = {'base': f'{BASE}/v2/catalogs/C/', 'resources': [{'id': 'a', 'subtype': 'image'}],
             'links': {'next': {'href': 'assets?page=2'}}}
    second = {'base': f'{BASE}/v2/catalogs/C/', 'resources': [{'id': 'b', 'subtype': 'image'}]}
    client, _, _ = make({('GET', f'{BASE}/v2/catalogs/C/assets'): [ok(first)],
                         ('GET', f'{BASE}/v2/catalogs/C/assets?page=2'): [ok(second)]})
    pages = []
    client.on_page = lambda: pages.append(1)
    run(client.assets('C'))
    assert len(pages) == 2
