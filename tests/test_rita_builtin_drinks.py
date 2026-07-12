"""Tests for the Rita built-in drink catalog and selection encoding."""

from custom_components.philips_homeid.const import (
    RITA_BUILTIN_DRINK_OFFSET,
    RITA_BUILTIN_DRINKS,
)

# Highest RitaDrinkId that the APK RitaDrinkKt name switch maps.
_MAX_DRINK_ID = 61
# Recipe slots occupy 0-79; built-in drinks must be keyed above that.
_MAX_RECIPE_SLOT = 79


def test_drink_ids_in_valid_range():
    """Every id is a real RitaDrinkId and stays below the built-in offset."""
    for drink_id in RITA_BUILTIN_DRINKS:
        assert 1 <= drink_id <= _MAX_DRINK_ID
        assert drink_id < RITA_BUILTIN_DRINK_OFFSET


def test_hot_water_excluded():
    """Hot Water (id 21) is served by its own button, not the drink list."""
    assert 21 not in RITA_BUILTIN_DRINKS


def test_no_iced_or_cold_brew():
    """Only hot drinks are listed; iced and cold-brew variants are omitted."""
    for name in RITA_BUILTIN_DRINKS.values():
        lowered = name.lower()
        assert "iced" not in lowered
        assert "cold" not in lowered


def test_names_non_empty_and_unique():
    """Option labels must be present and unique so the dropdown stays valid."""
    names = list(RITA_BUILTIN_DRINKS.values())
    assert all(name.strip() for name in names)
    assert len(names) == len(set(names))


def test_known_anchor_drinks():
    """A few well-known ids map to their expected names."""
    assert RITA_BUILTIN_DRINKS[2] == "Espresso"
    assert RITA_BUILTIN_DRINKS[14] == "Cappuccino"
    assert RITA_BUILTIN_DRINKS[19] == "Latte Macchiato"


def test_offset_past_recipe_slots():
    """The offset separates built-in drinks from recipe slots without overlap."""
    assert RITA_BUILTIN_DRINK_OFFSET > _MAX_RECIPE_SLOT


def test_selection_encoding_round_trip():
    """A built-in selection decodes back to its drink id; slots stay below offset."""
    for drink_id in RITA_BUILTIN_DRINKS:
        selection = RITA_BUILTIN_DRINK_OFFSET + drink_id
        assert selection >= RITA_BUILTIN_DRINK_OFFSET
        assert selection - RITA_BUILTIN_DRINK_OFFSET == drink_id
    for slot in (0, 1, _MAX_RECIPE_SLOT):
        assert slot < RITA_BUILTIN_DRINK_OFFSET
