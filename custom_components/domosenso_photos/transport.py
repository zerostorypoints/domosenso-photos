"""The one HTTP transport the integration injects into its clients (no Home Assistant import)."""
from __future__ import annotations

import asyncio

import aiohttp

from .album_share import MAX_BODY, Transport, Unreachable

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=900, sock_connect=30, sock_read=60)
CHUNK = 1 << 20


def make_transport(session: aiohttp.ClientSession, with_headers: bool = False) -> Transport:
    """(status, content type, body); with_headers adds the response headers as a plain dict
    (the Lightroom client reads Retry-After from them)."""
    async def transport(method, url, params=None, data=None, headers=None):
        """Redirects are not followed (the client reports them); bodies over MAX_BODY are refused."""
        try:
            async with session.request(method, url, params=params, data=data, headers=headers,
                                       timeout=REQUEST_TIMEOUT, allow_redirects=False) as resp:
                if (resp.content_length or 0) > MAX_BODY:
                    raise Unreachable('Response too large', 'too_large')
                body = bytearray()
                async for chunk in resp.content.iter_chunked(CHUNK):
                    body += chunk
                    if len(body) > MAX_BODY:
                        raise Unreachable('Response too large', 'too_large')
                if with_headers:
                    return resp.status, resp.headers.get('Content-Type', ''), bytes(body), dict(resp.headers)
                return resp.status, resp.headers.get('Content-Type', ''), bytes(body)
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise OSError(type(err).__name__) from err  # the client maps OSError to Unreachable
    return transport
