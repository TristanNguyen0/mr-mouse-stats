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


class TestDigitsInsideModelNames:
    """A digit run only ends a model when it starts a token.

    Both strings below are verbatim captures that were being mangled: the
    model truncated to "G", and — worse — a model number landing in the DPI
    column, since 400 is one of the canonical DPI steps.
    """

    def test_model_number_survives(self):
        parsed = parse_settings("logitech G502 X wireless")  # veswa
        assert parsed.mouse_brand == "Logitech"
        assert parsed.mouse_model == "G502 X wireless"

    def test_model_number_with_a_suffix_survives(self):
        parsed = parse_settings("Logitech G640 x NAVI")  # terramr
        assert parsed.mouse_model == "G640 x NAVI"

    def test_a_model_number_is_not_a_dpi(self):
        assert parse_settings("Logitech G400").dpi is None

    def test_a_trailing_bare_number_is_still_a_dpi(self):
        parsed = parse_settings("Logitech G Pro 800")
        assert parsed.dpi == 800
        assert parsed.mouse_model == "G Pro"

    def test_a_settings_clause_still_ends_the_model(self):
        parsed = parse_settings("Razer Viper V3 Pro, 1600 dpi")
        assert parsed.mouse_model == "Viper V3 Pro"
        assert parsed.dpi == 1600


# Bare formats below are verbatim from real captures (twitch_messages).


def test_bare_decimal_then_bare_dpi():
    parsed = parse_settings("0.85 1600")  # sparkchieff
    assert parsed.dpi == 1600
    assert parsed.sensitivity == 0.85


def test_bare_dpi_then_bare_decimal():
    parsed = parse_settings("1600 0.52")  # pkymr
    assert parsed.dpi == 1600
    assert parsed.sensitivity == 0.52


def test_dpi_keyword_with_bare_decimal():
    parsed = parse_settings("800 dpi 1.9 raw input on")  # sparkr_
    assert parsed.dpi == 800
    assert parsed.sensitivity == 1.9


def test_decimal_before_dpi_keyword():
    parsed = parse_settings("1.50 , 1600 dpi")  # kqzfps
    assert parsed.dpi == 1600
    assert parsed.sensitivity == 1.5


def test_ingame_suffix_without_sens_keyword():
    parsed = parse_settings("1600dpi 1.0 ingame")  # smashnezz
    assert parsed.dpi == 1600
    assert parsed.sensitivity == 1.0


def test_name_prefix_and_attached_dpi():
    parsed = parse_settings("tokyoism 0.52 2000DPI")  # jur3ky
    assert parsed.dpi == 2000
    assert parsed.sensitivity == 0.52


def test_bare_int_off_dpi_grid_not_taken():
    parsed = parse_settings("0.52 played 1700 hours")
    assert parsed.dpi is None
    assert parsed.sensitivity == 0.52


def test_two_bare_decimals_ambiguous():
    parsed = parse_settings("0.48 hitscan 0.69 projectile 1600 dpi")
    assert parsed.dpi == 1600
    assert parsed.sensitivity is None  # ambiguous, kept for smarter re-parse


def test_polling_rate_hz_not_mistaken_for_dpi():
    parsed = parse_settings("0.85 with 2000hz polling")
    assert parsed.dpi is None
    assert parsed.sensitivity == 0.85


def test_brand_only():
    parsed = parse_settings("he's on the Vaxee XE right now")
    assert parsed.mouse_brand == "Vaxee"
    assert parsed.mouse_model == "XE"  # prose words after the model are cut
    assert parsed.dpi is None
