"""Seeded corpus of chatbot response formats; grown from real captures
as they accrue (raw text is kept in twitch_messages for re-parsing)."""

from mr_mouse_stats.twitch.settings_parse import parse_settings


def test_full_nightbot_style_line():
    parsed = parse_settings(
        "energy: 800 DPI, 6 in-game sens, win 6, Finalmouse Starlight Pro TenZ"
    )
    assert parsed.dpi == 800
    assert parsed.sensitivity == 6.0
    assert parsed.windows_sens == 6
    assert parsed.mouse_brand == "Finalmouse"
    assert parsed.mouse_model == "Starlight Pro TenZ"


def test_pipe_separated_streamelements_style():
    parsed = parse_settings("@viewer -> DPI: 1600 | Sens: 3.2 | Mouse: Lamzu Atlantis Mini")
    assert parsed.dpi == 1600
    assert parsed.sensitivity == 3.2
    assert parsed.mouse_brand == "Lamzu"
    assert parsed.mouse_model == "Atlantis Mini"


def test_terse_response():
    parsed = parse_settings("800dpi 7 sens")
    assert parsed.dpi == 800
    assert parsed.sensitivity == 7.0
    assert parsed.windows_sens is None


def test_mouse_first_ordering():
    parsed = parse_settings("Mouse: Logitech G Pro X Superlight 2, 1600 DPI, sens 2.5")
    assert parsed.mouse_brand == "Logitech"
    assert parsed.mouse_model == "G Pro X Superlight"  # trailing "2" hits digit stop
    assert parsed.dpi == 1600
    assert parsed.sensitivity == 2.5


def test_prose_with_brand_lowercase():
    parsed = parse_settings("he uses razer viper v3 pro, 1600 dpi")
    assert parsed.mouse_brand == "Razer"
    assert parsed.mouse_model == "viper v3 pro"
    assert parsed.dpi == 1600
    assert parsed.sensitivity is None


def test_windows_sens_does_not_leak_into_game_sens():
    parsed = parse_settings("windows sens 6, in-game 800 dpi")
    assert parsed.windows_sens == 6
    assert parsed.sensitivity is None
    assert parsed.dpi == 800


def test_decimal_sensitivity():
    parsed = parse_settings("sensitivity: 0.45, 3200 dpi")
    assert parsed.sensitivity == 0.45
    assert parsed.dpi == 3200


def test_no_settings_content_returns_none():
    assert parse_settings("what dpi do you use?") is None
    assert parse_settings("nice aim dude") is None
    assert parse_settings("!dpi") is None


def test_brand_only():
    parsed = parse_settings("he's on the Vaxee XE right now")
    assert parsed.mouse_brand == "Vaxee"
    assert parsed.mouse_model == "XE"  # prose words after the model are cut
    assert parsed.dpi is None
