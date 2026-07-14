"""Tests for parsing the Rita cloud drink-capabilities response."""

from custom_components.philips_homeid.coordinator import PhilipsHomeIDCoordinator

_parse = PhilipsHomeIDCoordinator._parse_rita_capabilities

_CTN = "EP8757/90"
_ITEMS = [
    {"drinkID": 2, "drinkName": "Espresso", "ctnNumbers": [_CTN, "EP8758/90"]},
    {"drinkID": 14, "drinkName": "Cappuccino", "ctnNumbers": [_CTN]},
    {"drinkID": 21, "drinkName": "Hot Water", "ctnNumbers": [_CTN]},  # excluded
    {"drinkID": 99, "drinkName": "Other Only", "ctnNumbers": ["EP9999/90"]},  # wrong ctn
    {"drinkID": 5, "drinkName": "  Double Espresso  ", "ctnNumbers": [_CTN]},  # trimmed
]


def test_filters_by_ctn():
    cat = _parse(_ITEMS, _CTN)
    assert 2 in cat and 14 in cat
    assert 99 not in cat  # ctn not in that drink's ctnNumbers


def test_hot_water_excluded():
    assert 21 not in _parse(_ITEMS, _CTN)


def test_name_is_trimmed():
    assert _parse(_ITEMS, _CTN)[5] == "Double Espresso"


def test_known_names():
    cat = _parse(_ITEMS, _CTN)
    assert cat[2] == "Espresso"
    assert cat[14] == "Cappuccino"


def test_no_ctn_match_returns_empty():
    assert _parse(_ITEMS, "EP0000/00") == {}


def test_empty_and_malformed_inputs():
    assert _parse([], _CTN) == {}
    assert _parse("not a list", _CTN) == {}  # type: ignore[arg-type]
    # non-int id, missing id, empty name, non-list ctnNumbers, bool id
    bad = [
        {"drinkID": "x", "drinkName": "Bad", "ctnNumbers": [_CTN]},
        {"drinkName": "No Id", "ctnNumbers": [_CTN]},
        {"drinkID": 3, "drinkName": "", "ctnNumbers": [_CTN]},
        {"drinkID": 4, "drinkName": "X", "ctnNumbers": "notlist"},
        {"drinkID": True, "drinkName": "Truthy", "ctnNumbers": [_CTN]},
    ]
    assert _parse(bad, _CTN) == {}
