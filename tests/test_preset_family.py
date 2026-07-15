"""Tests for picking the cooking-method table that matches the appliance."""

from unittest.mock import MagicMock

from custom_components.philips_homeid.coordinator import PhilipsHomeIDCoordinator
from custom_components.philips_homeid.local_api import (
    PORT_HERMESAC,
    PORT_NUTRIMAX,
    PORT_VENUSAF,
)
from custom_components.philips_homeid.local_models import LocalDeviceInfo
from custom_components.philips_homeid.select import (
    HERMES_PRESETS,
    NUTRIMAX_PRESETS,
    SPECTRE_PRESETS,
    VENUS_PRESETS,
    PhilipsHomeIDCookingMethodSelect,
)


def _coordinator(airfryer_port=None, model="", mqtt_is_venus=None):
    coordinator = PhilipsHomeIDCoordinator.__new__(PhilipsHomeIDCoordinator)
    coordinator.device_info = LocalDeviceInfo(
        ip_address="", cpp_id="aabb", model_name=model
    )
    coordinator.device_info.airfryer_port = airfryer_port
    if mqtt_is_venus is None:
        coordinator.mqtt_client = None
    else:
        client = MagicMock()
        client.is_venus = mqtt_is_venus
        coordinator.mqtt_client = client
    return coordinator


def _presets(coordinator):
    select = PhilipsHomeIDCookingMethodSelect.__new__(PhilipsHomeIDCookingMethodSelect)
    select.coordinator = coordinator
    return select._get_presets()


def test_local_venus_uses_the_venus_table():
    assert _presets(_coordinator(airfryer_port=PORT_VENUSAF)) is VENUS_PRESETS


def test_local_nutrimax_uses_the_nutrimax_table():
    assert _presets(_coordinator(airfryer_port=PORT_NUTRIMAX)) is NUTRIMAX_PRESETS


def test_local_hermes_uses_the_hermes_table():
    assert _presets(_coordinator(airfryer_port=PORT_HERMESAC)) is HERMES_PRESETS


def test_local_spectre_uses_the_spectre_table():
    assert _presets(_coordinator(airfryer_port="airfryer")) is SPECTRE_PRESETS


def test_fusion_venus_uses_the_venus_table():
    """A Venus on FUSION has no airfryer_port to read.

    It used to fall through to the SPECTRE table, where the same ids name
    different cooking methods: preset 1 showed as frozen_snacks instead of
    auto_cook, and picking chicken wrote 3, which Venus reads as recipe.
    """
    assert _presets(_coordinator(model="HD9880", mqtt_is_venus=True)) is VENUS_PRESETS


def test_fusion_spectre_still_uses_the_spectre_table():
    assert (
        _presets(_coordinator(model="HD9280", mqtt_is_venus=False)) is SPECTRE_PRESETS
    )


def test_fusion_nutrimax_uses_the_nutrimax_table():
    """Nutrimax carries the Venus extras, so the ports alone would say Venus.

    The model is the only thing that tells it apart on FUSION.
    """
    assert (
        _presets(_coordinator(model="NX0960", mqtt_is_venus=True)) is NUTRIMAX_PRESETS
    )


def test_fusion_hermes_uses_the_hermes_table():
    assert _presets(_coordinator(model="NX0950", mqtt_is_venus=True)) is HERMES_PRESETS


def test_an_unknown_fusion_model_falls_back_to_the_ports():
    """Venus is still worth recognising for a model we do not have listed."""
    assert _presets(_coordinator(model="HD9999", mqtt_is_venus=True)) is VENUS_PRESETS


def test_venus_and_spectre_disagree_about_preset_one():
    """The premise of the fix: the tables are not interchangeable."""
    assert SPECTRE_PRESETS[1] == "frozen_snacks"
    assert VENUS_PRESETS[1] == "auto_cook"
