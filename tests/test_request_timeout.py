"""Tests for the timeout budget of a local request.

A ClientTimeout carrying only a total budget reports every kind of hang as a
bare asyncio TimeoutError, and that exception has no message at all. An
appliance whose web server wedges accepts the socket and then never finishes
the TLS handshake, which is the shape of failure this integration sees most,
so the log filled with error lines that ended at the colon. Splitting a
connect budget off the total turns those into an aiohttp ConnectionTimeoutError
that names itself.
"""

import asyncio
import logging

import aiohttp
import pytest

from custom_components.philips_homeid import local_api
from custom_components.philips_homeid.local_models import LocalDeviceInfo

LOGGER_NAME = "custom_components.philips_homeid.local_api"


def test_connect_budget_is_shorter_than_the_total():
    """The connect budget only helps while it expires before the total does."""
    assert local_api.REQUEST_TIMEOUT.connect is not None
    assert local_api.REQUEST_TIMEOUT.total is not None
    assert local_api.REQUEST_TIMEOUT.connect < local_api.REQUEST_TIMEOUT.total


@pytest.mark.asyncio
async def test_a_socket_that_never_answers_is_named_in_the_log(monkeypatch, caplog):
    """A server that accepts and then goes quiet must log what went wrong.

    This is the failure a wedged appliance produces: the TCP connect works,
    the TLS handshake gets no reply, and the request hangs until it is cut
    off. Without the connect budget the line named neither the timeout nor
    anything else.
    """
    stop = asyncio.Event()

    async def hold(reader, writer):
        # Hold the connection open without answering, then let go so the
        # server can shut down at the end of the test.
        await stop.wait()
        writer.close()

    server = await asyncio.start_server(hold, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setattr(
        local_api, "REQUEST_TIMEOUT", aiohttp.ClientTimeout(total=5, connect=0.2)
    )

    api = local_api.PhilipsLocalAPI()
    device = LocalDeviceInfo(ip_address=f"127.0.0.1:{port}", cpp_id="")
    try:
        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            result = await api._request(device, "airfryer")
    finally:
        await api.close()
        stop.set()
        server.close()
        await server.wait_closed()

    assert result is None
    messages = [r.getMessage() for r in caplog.records if r.name == LOGGER_NAME]
    assert any("Connection timeout" in message for message in messages), messages
