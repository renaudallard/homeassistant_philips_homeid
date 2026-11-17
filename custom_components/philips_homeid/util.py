"""Shared helpers for the Philips HomeID integration."""

from __future__ import annotations

import zlib

import aiohttp


def gunzip(data: bytes, limit: int) -> bytes | None:
    """One gzip member inflated to at most ``limit`` bytes.

    None if it is not gzip, is cut short or would inflate to more.
    """
    inflate = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        plain = inflate.decompress(data, limit)
    except zlib.error:
        return None
    if not plain or inflate.unconsumed_tail or not inflate.eof:
        return None
    return plain


async def read_reply(response: aiohttp.ClientResponse, limit: int) -> bytes | None:
    """The body of a reply, at most ``limit`` bytes both as sent and inflated.

    None if it is larger or in an encoding other than gzip. The request has
    to be made with ``auto_decompress=False`` and ``Accept-Encoding: gzip``:
    aiohttp inflates with no limit on the whole body, and before 3.13 with
    none on a chunk either, so a small gzip reply could take gigabytes.
    """
    # An empty header means none, as aiohttp reads it.
    encoding = response.headers.get("Content-Encoding", "")
    encoding = encoding.strip().lower() or "identity"
    if encoding not in ("identity", "gzip"):
        return None
    body = bytearray()
    async for chunk in response.content.iter_chunked(1 << 16):
        body += chunk
        if len(body) > limit:
            return None
    if encoding == "gzip":
        return gunzip(bytes(body), limit)
    return bytes(body)


def normalize_unique_id(raw_id: str) -> str:
    """Normalize a device identifier to a consistent format.

    Handles MAC addresses in various formats (colon-separated, dash-separated,
    bare hex, uppercase/lowercase) and UUIDs containing a MAC suffix.
    Normalizes to lowercase colon-separated MAC when possible.
    """
    if not raw_id:
        return raw_id
    raw_id = raw_id.strip().lower()
    # Strip UUID prefix: "12345678-1234-1234-1234-e4bc960f7d9d" -> "e4bc960f7d9d"
    if len(raw_id) == 36 and raw_id.count("-") == 4:
        raw_id = raw_id.rsplit("-", 1)[-1]
    # Remove separators to get bare hex
    bare = raw_id.replace(":", "").replace("-", "")
    # If it's 12 hex chars, format as colon-separated MAC
    if len(bare) == 12 and all(c in "0123456789abcdef" for c in bare):
        return ":".join(bare[i : i + 2] for i in range(0, 12, 2))
    return raw_id
