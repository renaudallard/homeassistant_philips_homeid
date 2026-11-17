"""Tests for the shared gzip helper."""

import gzip
import zlib

import pytest

from custom_components.philips_homeid import util
from custom_components.philips_homeid.util import gunzip

LIMIT = 1 << 10


def test_every_member_is_read():
    data = gzip.compress(b'{"item":[') + gzip.compress(b'{"id":"h"}]}')
    assert gunzip(data, LIMIT) == b'{"item":[{"id":"h"}]}'


def test_members_share_one_limit():
    half = gzip.compress(b"a" * (LIMIT // 2))
    assert gunzip(half * 2, LIMIT) == b"a" * LIMIT
    assert gunzip(half * 2 + gzip.compress(b"a"), LIMIT) is None


def test_last_member_may_not_go_over_the_limit():
    first = gzip.compress(b"a" * (LIMIT - 1))
    assert gunzip(first + gzip.compress(b"a"), LIMIT) == b"a" * LIMIT
    assert gunzip(first + gzip.compress(b"a" * LIMIT), LIMIT) is None


def test_limit_may_be_filled_exactly():
    full = gzip.compress(b"a" * LIMIT)
    assert gunzip(full, LIMIT) == b"a" * LIMIT
    assert gunzip(full + gzip.compress(b""), LIMIT) == b"a" * LIMIT
    assert gunzip(gzip.compress(b"a" * (LIMIT + 1)), LIMIT) is None


@pytest.mark.parametrize("tail", [b"junk", b"\0\0", b"\x1f", gzip.compress(b"xx")[:-4]])
def test_bytes_after_a_member_that_are_not_one_are_refused(tail):
    assert gunzip(gzip.compress(b"{}") + tail, LIMIT) is None


def test_inflate_never_asks_for_more_than_the_room_left(monkeypatch):
    # The output alone does not show it: asking for 0 means no limit, and a
    # bomb after a full first member would be inflated whole before refused.
    asked = []
    real = zlib.decompressobj

    class Spy:
        def __init__(self, wbits):
            self._inflate = real(wbits)

        def decompress(self, data, max_length):
            asked.append(max_length)
            return self._inflate.decompress(data, max_length)

        def __getattr__(self, name):
            return getattr(self._inflate, name)

    monkeypatch.setattr(util.zlib, "decompressobj", Spy)
    half = gzip.compress(b"a" * (LIMIT // 2))
    assert gunzip(half * 2 + gzip.compress(b""), LIMIT) == b"a" * LIMIT
    assert asked == [LIMIT + 1, LIMIT // 2 + 1, 1]


def test_member_cut_short_is_refused():
    assert gunzip(gzip.compress(b"{}")[:-4], LIMIT) is None


def test_number_of_members_is_bounded():
    member = gzip.compress(b" ")
    assert gunzip(member * util._MAX_MEMBERS, LIMIT) == b" " * util._MAX_MEMBERS
    assert gunzip(member * (util._MAX_MEMBERS + 1), LIMIT) is None


@pytest.mark.parametrize("data", [b"", b"{}"])
def test_not_gzip_is_refused(data):
    assert gunzip(data, LIMIT) is None


def test_empty_member_is_empty():
    assert gunzip(gzip.compress(b""), LIMIT) == b""


# What Java's GZIPInputStream, which the app's Tuya client uses, read on each.
@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (gzip.compress(b"{") + gzip.compress(b"}"), b"{}"),
        (gzip.compress(b"{}") + b"junk", b"{}"),
        (gzip.compress(b"{}") + b"\0" * 40, b"{}"),
        (gzip.compress(b"{}") + b"\x1f", b"{}"),
        (gzip.compress(b"{}") + b"\x1f\x8b", b"{}"),
        (gzip.compress(b"{}") + b"\x1f\x8b\x08", b"{}"),
        (gzip.compress(b"{}") + gzip.compress(b"{}")[:9], b"{}"),
        (gzip.compress(b"{}") + b"\x1f\x8b\x09" + b"\0" * 9, b"{}"),
        (gzip.compress(b"{}") + gzip.compress(b"{}")[:10], None),
        (gzip.compress(b"{}") + gzip.compress(b"xx")[:-4], None),
        (gzip.compress(b"{}")[:-4], None),
        (gzip.compress(b""), b""),
    ],
)
def test_ignore_trailing_reads_as_gzipinputstream(data, expected):
    assert gunzip(data, LIMIT, ignore_trailing=True) == expected


def test_ignore_trailing_keeps_the_limit_and_member_bound():
    over = gzip.compress(b"a" * LIMIT) + gzip.compress(b"a") + b"junk"
    assert gunzip(over, LIMIT, ignore_trailing=True) is None
    member = gzip.compress(b" ")
    many = member * util._MAX_MEMBERS + b"junk"
    assert gunzip(many, LIMIT, ignore_trailing=True) == b" " * util._MAX_MEMBERS
    many = member * (util._MAX_MEMBERS + 1)
    assert gunzip(many, LIMIT, ignore_trailing=True) is None
