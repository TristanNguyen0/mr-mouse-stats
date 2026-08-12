"""Canonical device names. The raw strings below are real ones observed in
chat — that variety is the whole reason this module exists."""

import pytest

from mr_mouse_stats.site.devices import canonical_mouse


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Logitech G PRO X Superlight", "Logitech G Pro X Superlight"),
        ("gpx superlight", "Logitech G Pro X Superlight"),
        ("gpro superlight", "Logitech G Pro X Superlight"),
        ("gpw superlight", "Logitech G Pro X Superlight"),
        ("Logitech G PRO X Superlight 2", "Logitech G Pro X Superlight 2"),
        ("Gpro Superlight 2", "Logitech G Pro X Superlight 2"),
        ("G-PRO WIRELESS", "Logitech G Pro Wireless"),
        ("Logitech g pro wireless", "Logitech G Pro Wireless"),
        ("Logitech G PRO X2 SUPERSTRIKE", "Logitech G Pro X2 Superstrike"),
        ("pro x2 superstrike", "Logitech G Pro X2 Superstrike"),
        ("Logitech G502 X wireless", "Logitech G502 X"),
        ("Razer Viper V4 Pro", "Razer Viper V4 Pro"),
        ("Razer viper v4 pro 4k hz poling rate", "Razer Viper V4 Pro"),
        ("Razer viper v3 pro", "Razer Viper V3 Pro"),
        ("Razer Viper V3", "Razer Viper V3"),
        ("Finalmouse ULX Competition", "Finalmouse ULX"),
        ("Final mouse ULX sakura", "Finalmouse ULX"),
        ("ulx medium", "Finalmouse ULX"),
        ("Final Mouse Frostlord Small", "Finalmouse Frostlord"),
        ("Pulsar x2 cl", "Pulsar X2"),
        ("Vaxee zygen np01s v2", "Vaxee Zygen NP01S"),
        ("Xtrfy m8", "Xtrfy M8"),
    ],
)
def test_spellings_of_the_same_mouse_collapse(raw, expected):
    assert canonical_mouse(raw) == expected


def test_specific_models_beat_general_ones():
    """Ordering, not luck: "superlight 2" must not be read as "superlight",
    and "viper v3 pro" must not be read as "viper v3"."""
    assert canonical_mouse("superlight 2") != canonical_mouse("superlight")
    assert canonical_mouse("viper v3 pro") != canonical_mouse("viper v3")


def test_unknown_mouse_is_kept_tidied_rather_than_guessed():
    assert canonical_mouse("beast x mini pro") == "Beast X Mini Pro"


def test_non_mice_are_dropped():
    """`!mouse` answers sometimes list the whole desk; the ranking is mice."""
    assert canonical_mouse("Logitech G640 x NAVI") is None
    assert canonical_mouse("Artisan Zero Soft") is None


def test_missing_and_empty_names():
    assert canonical_mouse(None) is None
    assert canonical_mouse("") is None
    assert canonical_mouse("   ") is None
