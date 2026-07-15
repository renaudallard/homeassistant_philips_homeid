"""Tests for matching a FUSION entry by IP across the host-form change."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from homeassistant.data_entry_flow import AbortFlow

from custom_components.philips_homeid.config_flow import PhilipsHomeIDConfigFlow
from custom_components.philips_homeid.const import CONF_CPP_ID, CONF_IS_FUSION


def _flow(stored_host):
    flow = PhilipsHomeIDConfigFlow()
    entry = SimpleNamespace(
        data={
            CONF_IS_FUSION: True,
            CONF_CPP_ID: "cloud-external-id",
            "host": stored_host,
        }
    )
    flow._async_current_entries = lambda: [entry]
    flow.hass = MagicMock()
    return flow


def test_a_bare_ipv6_entry_still_matches_a_bracketed_discovery():
    """Entries written before addresses were carried in URL form hold a bare
    address. Comparing that to a bracketed one would offer a device that is
    already set up as a new discovery, and nothing migrates the stored host.
    """
    flow = _flow("fd00::1")
    with pytest.raises(AbortFlow):
        flow._abort_if_device_already_configured("some-mac", "[fd00::1]")


def test_a_bracketed_entry_matches_a_bracketed_discovery():
    flow = _flow("[fd00::1]")
    with pytest.raises(AbortFlow):
        flow._abort_if_device_already_configured("some-mac", "[fd00::1]")


def test_ipv4_matching_is_unchanged():
    flow = _flow("192.0.2.10")
    with pytest.raises(AbortFlow):
        flow._abort_if_device_already_configured("some-mac", "192.0.2.10")


def test_a_different_device_still_does_not_match():
    flow = _flow("192.0.2.10")
    flow._abort_if_device_already_configured("some-mac", "192.0.2.99")
