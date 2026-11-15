"""Identity failures must not masquerade as an empty feeding history."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from custom_components.philips_homeid.feeder_meals import MealManager, MealsError


def manager_and_range(internal, external, registry_external="registry-external"):
    start = datetime(2026, 11, 13, tzinfo=UTC)
    request = AsyncMock(
        return_value={
            "item": [
                {
                    "id": "event",
                    "type": "meal_dispensed",
                    "deviceId": internal,
                    "deviceExternalId": external,
                    "time": start.isoformat(),
                }
            ]
        }
    )
    return (
        MealManager(
            request,
            "home",
            "philips-id",
            "tuya-id",
            "UTC",
            external_device_id=registry_external,
        ),
        start,
        start + timedelta(days=1),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "internal,external,registry_external",
    [
        ("philips-id", "external", None),
        ("philips-id", "different", "registry-external"),
        ("other-internal", "registry-external", "registry-external"),
        (None, "external", None),
        (None, None, "registry-external"),
        # Registry externalId absent: the Philips ID alone is not the pair.
        ("philips-id", None, None),
        # A foreign Philips ID paired with this feeder's vendor ID conflicts.
        ("foreign", "tuya-id", None),
    ],
)
async def test_ambiguous_own_or_unidentifiable_event_is_unavailable(
    internal, external, registry_external
):
    manager, start, end = manager_and_range(internal, external, registry_external)
    with pytest.raises(MealsError):
        await manager.history(start, end)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "internal,external,registry_external",
    [
        ("philips-id", "registry-external", "registry-external"),
        ("philips-id", "tuya-id", None),
        (None, "registry-external", "registry-external"),
    ],
)
async def test_known_ownership_remains_usable(internal, external, registry_external):
    manager, start, end = manager_and_range(internal, external, registry_external)
    assert len(await manager.history(start, end)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "internal,external",
    [
        ("foreign", "foreign-external"),
        ("foreign", None),
        (None, "foreign-external"),
    ],
)
async def test_genuinely_foreign_events_still_filter_cleanly(internal, external):
    manager, start, end = manager_and_range(internal, external)
    assert await manager.history(start, end) == []


@pytest.mark.asyncio
async def test_identity_diagnostic_contains_only_boolean_comparisons():
    from custom_components.philips_homeid.feeder_meals import EventOwnershipError

    manager, start, end = manager_and_range("philips-id", "tuya-id")
    with pytest.raises(EventOwnershipError) as failure:
        await manager.history(start, end)
    assert failure.value.flags == (True, True, True, True, False, False, True)
    assert all(type(flag) is bool for flag in failure.value.flags)
    assert "philips-id" not in str(failure.value)
    assert "tuya-id" not in str(failure.value)


@pytest.mark.asyncio
async def test_live_homeid_pair_when_registry_external_id_is_absent():
    """Observed shape1101001: both independently verified IDs match this device."""
    manager, start, end = manager_and_range("philips-id", "tuya-id", None)
    assert len(await manager.history(start, end)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "internal,external",
    [
        (None, "tuya-id"),
        ("philips-id", "unverified"),
        ("philips-id", None),
        (None, None),
    ],
)
async def test_live_homeid_compatibility_requires_both_verified_ids(internal, external):
    manager, start, end = manager_and_range(internal, external, None)
    with pytest.raises(MealsError):
        await manager.history(start, end)


@pytest.mark.asyncio
async def test_live_homeid_pair_never_overrides_a_known_conflicting_external_id():
    manager, start, end = manager_and_range(
        "philips-id", "tuya-id", "different-known-external"
    )
    with pytest.raises(MealsError):
        await manager.history(start, end)


@pytest.mark.asyncio
async def test_foreign_philips_id_without_vendor_id_filters_when_registry_has_none():
    manager, start, end = manager_and_range("foreign", None, None)
    assert await manager.history(start, end) == []
