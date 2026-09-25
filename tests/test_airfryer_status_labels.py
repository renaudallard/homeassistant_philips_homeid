"""Tests for the display labels of the airfryer status sensors.

Without state translations Home Assistant shows the raw device value, so the
logbook read "Cooking Status changed to user_action" whenever the drawer was
opened. The labels are display only: automations still match the raw value.
"""

import json
from pathlib import Path

import pytest

from custom_components.philips_homeid import local_models

BASE = Path(__file__).parent.parent / "custom_components" / "philips_homeid"
STATUSES = {
    value
    for name, value in vars(local_models).items()
    if name.startswith("AIRFRYER_STATUS_") and isinstance(value, str)
}
FILES = [
    BASE / "strings.json",
    BASE / "translations/en.json",
    BASE / "translations/nl.json",
]


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
@pytest.mark.parametrize("key", ["airfryer_status", "airfryer_previous_status"])
def test_every_known_status_has_a_label(path, key):
    states = json.loads(path.read_text())["entity"]["sensor"][key].get("state", {})
    assert STATUSES - states.keys() == set()


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_the_drawer_notification_has_a_label(path):
    states = json.loads(path.read_text())["entity"]["sensor"]["airfryer_dialog"].get(
        "state", {}
    )
    assert "close_drawer" in states


def test_strings_and_english_agree():
    assert json.loads((BASE / "strings.json").read_text()) == json.loads(
        (BASE / "translations/en.json").read_text()
    )
