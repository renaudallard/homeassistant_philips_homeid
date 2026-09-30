"""Every value an enum-decoded sensor can report has a display label.

Without a state translation Home Assistant shows the raw decoded value, so the
firmware update sensor read "no_download". The labels are display only:
automations still match the raw value.
"""

import json
from pathlib import Path

import pytest

from custom_components.philips_homeid import sensor_descriptions as sd

BASE = Path(__file__).parent.parent / "custom_components" / "philips_homeid"
FILES = [BASE / "strings.json", *sorted((BASE / "translations").glob("*.json"))]
SENSORS = {
    "espresso_mainstate": sd._ESPRESSO_MAINSTATE,
    "rita_machine_state": sd._RITA_MACHINE_STATE,
    "rita_machine_status": sd._RITA_MACHINE_STATUS,
    "rita_machine_extra_info": sd._RITA_MACHINE_EXTRA_INFO,
    "rita_control_status": sd._RITA_CONTROL_STATUS,
    "rita_bean_type": sd._RITA_BEAN_TYPE,
    "rita_roast_level": sd._RITA_ROAST_LEVEL,
    "ota_state": sd._OTA_STATE,
}


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
@pytest.mark.parametrize("key", SENSORS)
def test_every_decoded_value_has_a_label(path, key):
    states = json.loads(path.read_text())["entity"]["sensor"][key].get("state", {})
    assert set(SENSORS[key].values()) == states.keys()
    assert all(states.values())
