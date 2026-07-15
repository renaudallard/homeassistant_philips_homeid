"""Tests for the stale entity cleanup guard."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from custom_components.philips_homeid import _cleanup_stale_entities
from custom_components.philips_homeid.local_models import (
    LocalDeviceInfo,
    LocalDeviceState,
)

# Real sensor description keys, so the cleanup actually resolves them to a
# property. A key that matches no description is skipped and would make these
# tests pass no matter what the guard does.
PM1 = "pm1"
TVOC = "tvoc"


def _state(properties):
    return LocalDeviceState(
        device_info=LocalDeviceInfo(ip_address="192.0.2.10", cpp_id="aabb"),
        properties=properties,
    )


def _coordinator(state):
    """Build a coordinator stub whose has_property mirrors the real one."""

    def has_property(property_key, nested_key=None):
        if not state or not property_key:
            return False
        if nested_key:
            nested = state.properties.get(nested_key)
            return bool(nested) and isinstance(nested, dict) and property_key in nested
        return property_key in state.properties

    return SimpleNamespace(
        device_state=state,
        device_info=LocalDeviceInfo(ip_address="192.0.2.10", cpp_id="aabb"),
        has_property=has_property,
    )


def _run_cleanup(coordinator, keys):
    registry = MagicMock()
    entries = [
        SimpleNamespace(
            unique_id=f"aabb_{key}", domain="sensor", entity_id=f"sensor.{key}"
        )
        for key in keys
    ]
    with (
        patch(
            "custom_components.philips_homeid.er.async_get",
            return_value=registry,
        ),
        patch(
            "custom_components.philips_homeid.er.async_entries_for_config_entry",
            return_value=entries,
        ),
    ):
        _cleanup_stale_entities(
            MagicMock(), SimpleNamespace(entry_id="e1"), coordinator
        )
    return registry


def test_no_state_removes_nothing():
    """A device that was offline at startup must keep its registry entries.

    device_state is None whenever the first refresh found nothing, which does
    not fail setup on its own. Cleanup then sees no properties at all and
    would otherwise consider every entity stale, taking the user's names,
    entity ids and area assignments with it.
    """
    registry = _run_cleanup(_coordinator(None), [PM1, TVOC])
    registry.async_remove.assert_not_called()


def test_known_airfryer_port_without_data_removes_nothing():
    """The pre-existing guard still holds once state is present but empty."""
    coordinator = _coordinator(_state({"firmware": {}}))
    coordinator.device_info.airfryer_port = "airfryer"
    registry = _run_cleanup(coordinator, [PM1])
    registry.async_remove.assert_not_called()


def test_reported_property_survives_cleanup():
    """An entity whose property is still reported is kept."""
    registry = _run_cleanup(_coordinator(_state({PM1: 3})), [PM1])
    registry.async_remove.assert_not_called()


def test_unreported_property_is_removed():
    """Cleanup still does its job once the device has reported its state."""
    registry = _run_cleanup(_coordinator(_state({PM1: 3})), [PM1, TVOC])
    registry.async_remove.assert_called_once_with(f"sensor.{TVOC}")
