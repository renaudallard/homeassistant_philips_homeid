"""Tests for what a failed local request writes to the log.

An exception is not guaranteed to carry a message. A timeout raises a bare
TimeoutError whose text is empty, and the log line then stopped at its colon
and said nothing about what had happened, for every endpoint of every poll.
"""

import logging
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.philips_homeid import local_api
from custom_components.philips_homeid.local_models import LocalDeviceInfo

LOGGER_NAME = "custom_components.philips_homeid.local_api"


def _api(err):
    """Build an API client whose every request raises err."""
    session = MagicMock()
    session.get = MagicMock(side_effect=err)
    session.put = MagicMock(side_effect=err)
    api = local_api.PhilipsLocalAPI()
    api._get_session = AsyncMock(return_value=session)
    return api


def _messages(caplog):
    return [r.getMessage() for r in caplog.records if r.name == LOGGER_NAME]


@pytest.mark.parametrize(
    ("err", "expected"),
    [
        (TimeoutError(), "TimeoutError"),
        (aiohttp.ServerDisconnectedError(), "ServerDisconnectedError"),
    ],
)
@pytest.mark.asyncio
async def test_a_message_less_failure_is_named_by_its_type(err, expected, caplog):
    """A failure with no message of its own still has to be identifiable."""
    api = _api(err)
    device = LocalDeviceInfo(ip_address="192.0.2.10", cpp_id="")

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        result = await api._request(device, "airfryer")

    assert result is None
    assert any(expected in message for message in _messages(caplog))


@pytest.mark.asyncio
async def test_a_failure_with_a_message_keeps_it(caplog):
    """The type name stands in for a missing message, it does not replace it."""
    api = _api(aiohttp.ClientConnectorError(MagicMock(), OSError(111, "refused")))
    device = LocalDeviceInfo(ip_address="192.0.2.10", cpp_id="")

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        await api._request(device, "airfryer")

    assert any("refused" in message for message in _messages(caplog))


@pytest.mark.asyncio
async def test_the_probe_names_its_failures_too(caplog):
    """The probe runs before anything else, so its log has to be readable."""
    api = _api(TimeoutError())
    device = LocalDeviceInfo(ip_address="192.0.2.10", cpp_id="")

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        data, status = await api._probe_request(device, "device")

    assert (data, status) == (None, None)
    assert any("TimeoutError" in message for message in _messages(caplog))
