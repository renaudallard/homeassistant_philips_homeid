"""Shared helpers for the Philips HomeID integration."""

from __future__ import annotations


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
