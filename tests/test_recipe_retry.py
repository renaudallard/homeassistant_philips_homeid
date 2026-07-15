"""Tests for classifying recipe-lookup failures as retryable or final."""

import pytest

from custom_components.philips_homeid.cloud_api import (
    CloudConnectionError,
    PhilipsCloudAPI,
)

_check = PhilipsCloudAPI._raise_if_retryable


@pytest.mark.parametrize("status", [500, 502, 503, 504, 429])
def test_retryable_statuses_raise(status):
    with pytest.raises(CloudConnectionError):
        _check(status, "Recipe lookup for 123")


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410])
def test_final_statuses_do_not_raise(status):
    _check(status, "Recipe lookup for 123")


def test_message_carries_context():
    with pytest.raises(CloudConnectionError, match="Recipe lookup for 123: HTTP 503"):
        _check(503, "Recipe lookup for 123")
