"""Tests for what the NCP send path logs."""

import logging

from custom_components.philips_homeid.mqtt_api import (
    FusionDeviceInfo,
    PhilipsMQTTClient,
)


class _FakePaho:
    def publish(self, topic, payload, qos):
        pass


def _venus1_client():
    client = PhilipsMQTTClient(
        FusionDeviceInfo(
            thing_name="da-test",
            device_id="test",
            tenant="da",
            mqtt_host="host",
            platform_rest_url="url",
            model_name="HD9875",
        )
    )
    client._client = _FakePaho()
    client._connected = True
    client._discovered_ports = ["Status", "devcurst_s"]
    client._discovered_write_ports = ["Control"]
    return client


def test_the_log_shows_the_properties_as_sent(caplog):
    """A Venus 1 shares the Control port name with SPECTRE and has its keys
    renamed on the way out. Logging the caller's dict showed time where the
    wire carried total_time, so an accepted and a refused write read alike.
    """
    client = _venus1_client()

    with caplog.at_level(logging.DEBUG, logger="custom_components.philips_homeid"):
        client.send_port_command("control", "setPort", {"time": 960})

    sent = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Sent")]
    assert sent == ["Sent setPort to da-test/control: {'total_time': 960}"]
