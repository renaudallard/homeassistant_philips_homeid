"""Tests for pulling a usable host out of a discovery message.

ip_address is carried in the form a URL wants, which is what
config_flow._sanitize_host produces for a typed-in host and what
local_api._build_url interpolates straight into the URL. Parsing the address
correctly is not enough on its own: the result has to build a valid URL.
"""

from aiohttp.client import URL

from custom_components.philips_homeid.local_api import PhilipsLocalAPI
from custom_components.philips_homeid.local_models import (
    parse_ssdp_device,
    parse_zeroconf_device,
)


def _ssdp(location):
    device = parse_ssdp_device(
        {"location": location, "udn": "uuid:1234", "modelName": "HD9280"}
    )
    return device.ip_address if device else None


def _zeroconf(host):
    device = parse_zeroconf_device(
        {
            "host": host,
            "name": "x._philipscondor._tcp.local.",
            "properties": {"id": "aabb"},
            "type": "_philipscondor._tcp.local.",
        }
    )
    return device.ip_address if device else None


def _url_is_valid(ip_address):
    device = parse_ssdp_device(
        {
            "location": f"http://{ip_address}/d.xml",
            "udn": "uuid:1",
            "modelName": "HD9280",
        }
    )
    url = PhilipsLocalAPI._build_url(PhilipsLocalAPI(), device, "status")
    try:
        URL(url)
    except ValueError:
        return False
    return True


def test_ipv4_with_a_port():
    assert _ssdp("http://192.0.2.10:80/upnp/description.xml") == "192.0.2.10"


def test_ipv4_without_a_port():
    assert _ssdp("http://192.0.2.10/upnp/description.xml") == "192.0.2.10"


def test_ipv6_keeps_the_whole_address():
    """Splitting on ':' cut this to "[fd00", which no request could reach."""
    assert _ssdp("http://[fd00::1234]:80/upnp/description.xml") == "[fd00::1234]"


def test_ipv6_without_a_port():
    assert _ssdp("http://[fd00::1234]/upnp/description.xml") == "[fd00::1234]"


def test_ipv6_builds_a_valid_url():
    """The point of the exercise: a bare address builds an unusable URL."""
    assert _url_is_valid("[fd00::1234]") is True


def test_ipv4_builds_a_valid_url():
    assert _url_is_valid("192.0.2.10") is True


def test_hostname_location():
    assert _ssdp("https://airfryer.local:443/di/v1/products/1/x") == "airfryer.local"


def test_location_without_a_scheme_is_rejected():
    assert _ssdp("192.0.2.10") is None


def test_empty_location_is_rejected():
    assert _ssdp("") is None


def test_zeroconf_ipv6_is_bracketed():
    """Zeroconf hands over a bare address, which _build_url cannot use."""
    assert _zeroconf("fd00::1234") == "[fd00::1234]"


def test_zeroconf_ipv4_is_untouched():
    assert _zeroconf("192.0.2.10") == "192.0.2.10"
