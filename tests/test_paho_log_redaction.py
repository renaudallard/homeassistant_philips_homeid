"""Tests for what the paho protocol trace forwards to the log."""

import logging
from types import SimpleNamespace

from custom_components.philips_homeid.mqtt_api import (
    FusionDeviceInfo,
    PhilipsMQTTClient,
)

_CONNECT = "Sending CONNECT (u0, p0, wr0, wq0, wf0, c1, k30)"


def _paho_log_lines(caplog, buf):
    client = PhilipsMQTTClient(
        FusionDeviceInfo(
            thing_name="da-test",
            device_id="test",
            tenant="da",
            mqtt_host="host",
            platform_rest_url="url",
        )
    )
    paho = SimpleNamespace()
    client._setup_callbacks(paho)

    with caplog.at_level(logging.DEBUG, logger="custom_components.philips_homeid"):
        paho.on_log(paho, None, 16, buf)

    return [r.getMessage() for r in caplog.records]


def test_connect_line_hides_the_client_id(caplog):
    """paho prints the whole client id on CONNECT, and its prefix is the
    Philips account id. This is the line paho 2.1.0 emits, verbatim.
    """
    lines = _paho_log_lines(
        caplog,
        f"{_CONNECT} client_id=b'us-east-1:1234abcd_0f0e0d0c-aaaa-bbbb-cccc-111122223333'",
    )

    assert lines == [f"paho: [16] {_CONNECT} client_id=<redacted>"]


def test_mqtt5_connect_line_keeps_the_properties(caplog):
    lines = _paho_log_lines(
        caplog, f"{_CONNECT} client_id=b'1234abcd_uuid' properties=None"
    )

    assert lines == [f"paho: [16] {_CONNECT} client_id=<redacted> properties=None"]


def test_other_lines_pass_through(caplog):
    lines = _paho_log_lines(caplog, "Received PINGRESP")

    assert lines == ["paho: [16] Received PINGRESP"]
