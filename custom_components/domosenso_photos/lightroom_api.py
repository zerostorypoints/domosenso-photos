"""Async, read-only Lightroom client (no Home Assistant import).

Every request towards Adobe is a GET: `_request` is the single send path and refuses any other method
before it reaches the transport. Nothing here generates renditions.
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from urllib.parse import urljoin

from .album_share import Transport
from .album_share import Unreachable as _TransportRefused

API_BASE = 'https://lr.adobe.io'
PREFIX = 'while (1) {}'
PAGE = 100
PAUSE = 0.2
BACKOFF = (2, 4, 8)
JPEG_MAGIC = b'\xff\xd8'
RENDITION_ERRORS = (401, 403, 404)
MAX_RETRY_AFTER = 300  # seconds; a longer Retry-After would hold the run for hours
MAX_PAGES = 10_000


class LightroomError(Exception):
    pass


class AuthRefused(LightroomError):
    """Adobe refused the token: the household has to sign in again."""


class Unreachable(LightroomError):
    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


class RenditionUnavailable(LightroomError):
    """This one rendition cannot be had; the rest of the library is unaffected.

    `transient` is true when the answer was a network error or 429/5xx after every retry: one is that
    photo's bad luck, a run of them is an outage (the caller decides).
    """

    def __init__(self, message: str, transient: bool = False):
        super().__init__(message)
        self.transient = transient


def _claims(jwt: object) -> dict:
    """Payload of a JWT, unverified: used only to read ids and dates the household's own token carries."""
    try:
        part = jwt.split('.')[1]  # type: ignore[union-attr]
        claims = json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))
    except (AttributeError, IndexError, ValueError, TypeError):
        return {}
    return claims if isinstance(claims, dict) else {}


def account_id(token: dict) -> str | None:
    """id_token `sub`, else access-token `user_id`, else access-token `sub`."""
    sub = _claims(token.get('id_token')).get('sub')
    if sub:
        return str(sub)
    access = _claims(token.get('access_token'))
    found = access.get('user_id') or access.get('sub')
    return str(found) if found else None


def signin_expires(token: dict) -> datetime | None:
    """The refresh token's own `created_at + expires_in` (ms, possibly strings)."""
    claims = _claims(token.get('refresh_token'))
    try:
        return datetime.fromtimestamp((int(claims['created_at']) + int(claims['expires_in'])) / 1000, UTC)
    except (KeyError, ValueError, TypeError, OverflowError, OSError):
        return None


class LightroomClient:
    def __init__(self, transport: Transport, client_id: str, token: Callable[[], Awaitable[str]],
                 sleep=asyncio.sleep, clock=time.monotonic, base: str = API_BASE):
        self._transport, self._client_id, self._token = transport, client_id, token
        self._sleep, self._clock, self._base = sleep, clock, base.rstrip('/')
        self._last: float | None = None
        self.on_page: Callable[[], None] | None = None  # told after every listing page (progress)

    async def _throttle(self) -> None:
        if self._last is not None:
            wait = PAUSE - (self._clock() - self._last)
            if wait > 0:
                await self._sleep(wait)
        self._last = self._clock()

    async def _request(self, method: str, url: str, params=None, headers=None):
        """The only send path. Returns (status, content type, body, response headers)."""
        if method != 'GET':
            raise ValueError('read-only')
        if not url.startswith(self._base + '/'):  # the token and the API key never leave Lightroom
            raise Unreachable('Request outside Lightroom', 'outside')
        sent = {'X-API-Key': self._client_id, 'Authorization': f'Bearer {await self._token()}', **(headers or {})}
        await self._throttle()
        try:
            res = await self._transport('GET', url, params=params, headers=sent)
        except _TransportRefused as err:
            raise Unreachable(str(err), err.code if hasattr(err, 'code') else None) from err
        status, ctype, body = res[:3]
        return status, ctype, body, (res[3] if len(res) > 3 and res[3] else {})

    async def _get(self, url: str, params=None, rendition: bool = False) -> bytes:
        """GET with the retry policy; returns the body of a 2xx, raises for everything else."""
        path = url.removeprefix(self._base)
        for attempt in range(len(BACKOFF) + 1):
            try:
                status, _ctype, body, headers = await self._request('GET', url, params=params)
            except OSError as err:
                problem, retry_after = f'{type(err).__name__} on {path}', None
            except Unreachable as err:
                if rendition and err.code == 'too_large':  # that photo's size, not an outage
                    raise RenditionUnavailable(f'Rendition too large on {path}') from err
                raise
            else:
                if 200 <= status < 300:
                    if rendition and (status == 202 or not body.startswith(JPEG_MAGIC)):
                        raise RenditionUnavailable(f'No rendition yet on {path} (HTTP {status})')
                    return body
                if 300 <= status < 400:
                    raise Unreachable(f'HTTP {status} on {path}', 'redirect')
                if rendition and status in RENDITION_ERRORS:
                    raise RenditionUnavailable(f'HTTP {status} on {path}')
                if status in (401, 403):
                    raise AuthRefused(f'Lightroom refused the token: HTTP {status} on {path}')
                if status != 429 and status < 500:
                    if rendition:
                        raise RenditionUnavailable(f'HTTP {status} on {path}')
                    raise Unreachable(f'HTTP {status} on {path}', 'client_error')
                problem = f'HTTP {status} on {path}'
                retry_after = next((v for k, v in headers.items() if k.lower() == 'retry-after'), None)
            if attempt == len(BACKOFF):
                if rendition:
                    raise RenditionUnavailable(problem, transient=True)
                raise Unreachable(problem)
            await self._sleep(min(int(retry_after), MAX_RETRY_AFTER) if str(retry_after or '').isdigit()
                              else BACKOFF[attempt])
        raise AssertionError('unreachable')  # pragma: no cover

    async def _json(self, url: str, params=None) -> dict:
        text = (await self._get(url, params)).decode('utf-8', 'replace')
        try:
            data = json.loads(text[len(PREFIX):] if text.startswith(PREFIX) else text)
        except ValueError as err:
            raise Unreachable('Response is not JSON') from err
        if not isinstance(data, dict):
            raise Unreachable('Response is not JSON')
        return data

    async def _pages(self, url: str, params) -> list[dict]:
        pages, seen = [], set()
        while True:
            page = await self._json(url, params)
            pages.append(page)
            if self.on_page:
                self.on_page()
            href = ((page.get('links') or {}).get('next') or {}).get('href')
            if not href:
                return pages
            url, params = urljoin(page.get('base') or self._base + '/', href), None
            if not url.startswith(self._base + '/'):  # never carry the token to another host
                raise Unreachable('Next page points outside Lightroom')
            if url in seen or len(pages) >= MAX_PAGES:  # a loop in the links: the listing never ends
                raise Unreachable('Paging does not end')
            seen.add(url)

    async def catalog_id(self) -> str:
        return (await self._json(f'{self._base}/v2/catalog'))['id']

    async def assets(self, catalog: str) -> list[dict]:
        pages = await self._pages(f'{self._base}/v2/catalogs/{catalog}/assets', {'subtype': 'image', 'limit': PAGE})
        return [a for p in pages for a in p.get('resources', []) if a.get('subtype') == 'image']

    async def albums(self, catalog: str) -> list[dict]:
        pages = await self._pages(f'{self._base}/v2/catalogs/{catalog}/albums', None)
        return [a for p in pages for a in p.get('resources', []) if a.get('subtype') in ('collection', 'collection_set')]

    async def album_asset_ids(self, catalog: str, album_id: str) -> list[str]:
        pages = await self._pages(f'{self._base}/v2/catalogs/{catalog}/albums/{album_id}/assets', {'limit': PAGE})
        return [r['asset']['id'] for p in pages for r in p.get('resources', [])]

    async def image(self, catalog: str, asset_id: str, max_edge: int) -> bytes:
        root = f'{self._base}/v2/catalogs/{catalog}/assets/{asset_id}/renditions'
        if max_edge > 2048:
            try:
                return await self._get(f'{root}/fullsize', rendition=True)
            except (RenditionUnavailable, Unreachable):
                pass  # an existing full-size rendition only; any failure falls back to the 2048 one
        return await self._get(f'{root}/2048', rendition=True)
