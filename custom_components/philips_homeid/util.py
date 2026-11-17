"""Shared helpers for the Philips HomeID integration."""

from __future__ import annotations

import zlib

import aiohttp

# zlib copies the rest of the input at the end of each member, so the
# number of members has to be bounded as well as their size.
_MAX_MEMBERS = 16


def gunzip(data: bytes, limit: int, *, ignore_trailing: bool = False) -> bytes | None:
    """Every gzip member inflated, at most ``limit`` bytes together.

    None if it is not gzip, would inflate to more, has a member cut short
    or has anything after a member that is not another one. With
    ``ignore_trailing``, bytes after a member that do not start with a whole
    gzip header end the input instead, as Java's GZIPInputStream reads it.
    """
    plain = b""
    for _ in range(_MAX_MEMBERS):
        inflate = zlib.decompressobj(16 + zlib.MAX_WBITS)
        try:
            # One byte more than the room left, so that a member too large
            # shows. It also never asks for 0, which zlib takes as no limit.
            plain += inflate.decompress(data, limit - len(plain) + 1)
        except zlib.error:
            return None
        if len(plain) > limit or not inflate.eof:
            return None
        data = inflate.unused_data
        if not data:
            return plain
        # GZIPInputStream stops at anything that is not a whole fixed header.
        if ignore_trailing and (len(data) < 10 or data[:3] != b"\x1f\x8b\x08"):
            return plain
    return None


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
    # An empty body is empty in any encoding, as aiohttp reads it.
    if encoding == "gzip" and body:
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
