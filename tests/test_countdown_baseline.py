"""Tests for the cook countdown extrapolation baseline."""

from types import SimpleNamespace

from custom_components.philips_homeid.coordinator import PhilipsHomeIDCoordinator


class _Stub:
    """Minimal stand-in exercising the real baseline logic."""

    _update_countdown_baseline = PhilipsHomeIDCoordinator._update_countdown_baseline

    def __init__(self):
        self._countdown_baseline = 0.0
        self._countdown_value = None


def _state(**airfryer):
    return SimpleNamespace(properties={"airfryer": airfryer})


def test_new_cur_time_moves_the_baseline():
    c = _Stub()
    c._update_countdown_baseline(_state(cur_time=600))
    first = c._countdown_baseline
    assert first > 0
    c._update_countdown_baseline(_state(cur_time=555))
    assert c._countdown_baseline > 0
    assert c._countdown_value == 555


def test_push_without_a_new_cur_time_keeps_the_baseline():
    c = _Stub()
    c._update_countdown_baseline(_state(cur_time=600))
    first = c._countdown_baseline
    # A later push carries the same cur_time forward (a delta that did not
    # touch it). Re-baselining here is what made the countdown jump back.
    c._update_countdown_baseline(_state(cur_time=600, status="cooking"))
    assert c._countdown_baseline == first


def test_unrelated_delta_during_a_cook_keeps_the_baseline():
    # The regression: mid-cook the device reports cur_time=600, then pushes a
    # devcurst_s delta carrying no cur_time. Deltas merge into a copy of the
    # previous state, so cur_time arrives unchanged. Re-baselining on that
    # message made the sensor render 600 again instead of counting down.
    c = _Stub()
    c._update_countdown_baseline(_state(cur_time=600, status="cooking"))
    first = c._countdown_baseline
    c._update_countdown_baseline(_state(cur_time=600, status="cooking", devcurst_s=1))
    assert c._countdown_baseline == first


def test_non_dict_airfryer_port_is_tolerated():
    c = _Stub()
    c._update_countdown_baseline(SimpleNamespace(properties={"airfryer": "junk"}))
    assert c._countdown_value is None
