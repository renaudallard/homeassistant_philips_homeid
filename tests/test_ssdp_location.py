"""Tests for pulling the host out of an SSDP location."""

from custom_components.philips_homeid.local_models import parse_ssdp_device


def _parse(location):
    device = parse_ssdp_device(
        {"location": location, "udn": "uuid:1234", "modelName": "HD9280"}
    )
    return device.ip_address if device else None


def test_ipv4_with_a_port():
    assert _parse("http://192.0.2.10:80/upnp/description.xml") == "192.0.2.10"


def test_ipv4_without_a_port():
    assert _parse("http://192.0.2.10/upnp/description.xml") == "192.0.2.10"


def test_ipv6_keeps_the_whole_address():
    """Splitting on ':' cut this to "[fd00", which no request could reach."""
    assert _parse("http://[fd00::1234]:80/upnp/description.xml") == "fd00::1234"


def test_ipv6_without_a_port():
    assert _parse("http://[fd00::1234]/upnp/description.xml") == "fd00::1234"


def test_hostname_location():
    assert _parse("https://airfryer.local:443/di/v1/products/1/x") == "airfryer.local"


def test_location_without_a_scheme_is_rejected():
    assert _parse("192.0.2.10") is None


def test_empty_location_is_rejected():
    assert _parse("") is None
