"""Tests for the Rita saved-recipe dropdown.

The blobs and the Rec_Names layout come from an EP8757/20 capture posted in
issue #15. Profile 1 lists recipe ids 2, 1, 4 and 414. Slots 8, 9 and 10 hold
built-in drinks personalised on the machine, which the machine stores without
a name; slot 11 holds a recipe built from scratch, which does carry one.
"""

from unittest.mock import MagicMock

from custom_components.philips_homeid.select import PhilipsHomeIDRitaBrewRecipeSelect

PROFILE1 = "COSHgcYCEAEiCQIBBJ4DAAAAAA=="
RECIPES = {
    8: "CAIQAhgeIAUwAUgC",  # recipeId 2, recipeBookId 2 (Espresso)
    9: "CAEQARgeIAMwAkgC",  # recipeId 1, recipeBookId 1 (Ristretto)
    10: "CAQQBBhQIAMwAUgB",  # recipeId 4, recipeBookId 4 (Espresso Lungo)
    11: "CJ4DEAEYIyADMAJIAg==",  # recipeId 414, recipeBookId 1, named
}
DRINKS = {1: "Ristretto", 2: "Espresso", 4: "Espresso Lungo"}


def _rec_names(named):
    """Build a 40 slot Rec_Names string with only the given slots named."""
    return ",".join(named.get(i, "") for i in range(40))


def _select(profile_blob=PROFILE1, recipes=None, names=None, profile_slot=1):
    sel = PhilipsHomeIDRitaBrewRecipeSelect.__new__(PhilipsHomeIDRitaBrewRecipeSelect)
    p1 = {"Rec_Names": _rec_names(names or {})}
    for slot in range(40):
        p1[f"rcp{slot}"] = (recipes or {}).get(slot, "")
    state = MagicMock()
    state.properties = {
        "Profiles": {"Pr_Names": "Romain,Test,,,,,,", "profile1": profile_blob},
        "Recipes_p1": p1,
        "Recipes_p2": {"Rec_Names": _rec_names({})},
    }
    coord = MagicMock()
    coord.device_state = state
    coord.rita_brew_profile_id = profile_slot
    coord.rita_builtin_drinks.return_value = dict(DRINKS)
    sel.coordinator = coord
    return sel


def test_unnamed_personalised_drinks_are_listed():
    """A profile holding only unnamed personalised drinks still has options."""
    sel = _select(recipes={8: RECIPES[8], 9: RECIPES[9], 10: RECIPES[10]})
    assert sel._slot_labels() == {
        8: "Espresso",
        9: "Ristretto",
        10: "Espresso Lungo",
    }


def test_named_recipe_keeps_its_own_name():
    """A recipe the machine named is listed under that name."""
    sel = _select(recipes=RECIPES, names={11: "Test recipe"})
    assert sel._slot_labels()[11] == "Test recipe"


def test_unnamed_recipe_falls_back_to_its_base_drink():
    """A nameless recipe built from scratch is named after its base drink."""
    sel = _select(recipes={11: RECIPES[11]})
    assert sel._slot_labels() == {11: "Ristretto"}


def test_unknown_drink_falls_back_to_the_slot_number():
    """A drink missing from the catalog still gets a usable label."""
    sel = _select(recipes={8: RECIPES[8]})
    sel.coordinator.rita_builtin_drinks.return_value = {}
    assert sel._slot_labels() == {8: "Recipe 8"}


def test_empty_slots_are_skipped():
    """Slots the profile does not reference stay out of the dropdown."""
    sel = _select(recipes={8: RECIPES[8]})
    assert sel._slot_labels() == {8: "Espresso"}


def test_profile_without_recipes_has_no_options():
    """An empty profile slot lists nothing rather than every slot."""
    sel = _select(profile_blob="GAkiCAAAAAAAAAAA", recipes=RECIPES)
    assert sel._slot_labels() == {}


def test_missing_ports_have_no_options():
    """A machine that has not reported its recipe ports lists nothing."""
    sel = _select(recipes=RECIPES)
    sel.coordinator.device_state.properties.pop("Recipes_p1")
    assert sel._slot_labels() == {}


def test_duplicate_drink_names_keep_their_slot_prefix():
    """Two personalised copies of one drink stay distinguishable."""
    sel = _select(recipes={8: RECIPES[8], 9: RECIPES[8]})
    assert sel._slot_labels() == {8: "8: Espresso", 9: "9: Espresso"}
