"""Tests for MUJI (FUSION) air purifier handling."""

from custom_components.philips_homeid.fan import (
    MUJI_MODE_MAPS,
    _muji_mode_map,
)
from custom_components.philips_homeid.mqtt_api import (
    FusionDeviceInfo,
    PhilipsMQTTClient,
)


def _make_client(model_name: str) -> PhilipsMQTTClient:
    """Build an MQTT client for a device without connecting."""
    device = FusionDeviceInfo(
        thing_name="da-test",
        device_id="test",
        tenant="da",
        mqtt_host="host",
        platform_rest_url="url",
        model_name=model_name,
    )
    return PhilipsMQTTClient(device)


def _ncp(port_name: str, properties: dict) -> dict:
    """Build an NCP port response payload."""
    return {"data": {"portName": port_name, "properties": properties}}


def test_air_purifier_status_flattened_to_top_level():
    """MUJI Status D-codes are stored at top level, not nested under airfryer."""
    client = _make_client("AC0650/10")
    client._handle_ncp_response(
        _ncp("Status", {"D0310C": 1, "D0310D": 2, "D03221": 12, "D03120": 3})
    )

    props = client._state.properties
    assert props["D0310C"] == 1
    assert props["D0310D"] == 2
    assert props["D03221"] == 12
    assert props["D03120"] == 3
    # Must not collide with the airfryer port mapping.
    assert "airfryer" not in props
    # Power flag: D0310D non-zero means on.
    assert client._state.power_on is True


def test_air_purifier_power_flag_off():
    """A zero D0310D fan-speed flag means the purifier is off."""
    client = _make_client("AC0651/10")
    client._handle_ncp_response(_ncp("Status", {"D0310D": 0}))
    assert client._state.power_on is False


def test_air_purifier_filter_port_flattened():
    """Filter (filtRd) D-codes are also stored at top level."""
    client = _make_client("AC0650/10")
    client._handle_ncp_response(_ncp("filtRd", {"D05207": 720, "D0520D": 500}))

    props = client._state.properties
    assert props["D05207"] == 720
    assert props["D0520D"] == 500
    assert "filtRd" not in props


def test_air_purifier_config_stays_nested():
    """Non data ports (Config) keep their nested layout for diagnostic sensors."""
    client = _make_client("AC0650/10")
    client._handle_ncp_response(_ncp("Config", {"name": "Living room", "serial": "X1"}))

    props = client._state.properties
    assert props["config"]["name"] == "Living room"
    assert props["config"]["serial"] == "X1"


def test_airfryer_status_stays_nested():
    """Airfryers are unaffected: Status still maps to the nested airfryer port."""
    client = _make_client("HD9280")
    client._handle_ncp_response(_ncp("Status", {"status": "cooking", "temp": 180}))

    props = client._state.properties
    assert props["airfryer"]["status"] == "cooking"
    assert props["airfryer"]["temp"] == 180
    assert client._state.power_on is True


def test_muji_mode_map_lookup():
    """Model names resolve to the correct operationMode map (or None)."""
    assert _muji_mode_map("AC0650/10") == {"gentle": 1, "sleep": 17, "turbo": 18}
    assert _muji_mode_map("AC0651/10")["auto"] == 0
    assert _muji_mode_map("AC1715/70")["fast"] == 2
    # Non air purifier models have no MUJI mode map.
    assert _muji_mode_map("HD9280") is None
    assert _muji_mode_map(None) is None


def test_muji_mode_values_unique_per_model():
    """Each model's operationMode values must be unique for reverse mapping."""
    for mode_map in MUJI_MODE_MAPS.values():
        values = list(mode_map.values())
        assert len(values) == len(set(values))
