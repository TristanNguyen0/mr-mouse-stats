import pytest

from mr_mouse_stats.nightbot.parse import (
    command_hint,
    normalize_name,
    parse_command,
    redact_variables,
)


@pytest.mark.parametrize(
    "raw,expected",
    [("!sens", "sens"), ("!Mouse ", "mouse"), ("nt", "nt"), ("!DPI", "dpi")],
)
def test_normalize_name(raw, expected):
    assert normalize_name(raw) == expected


# The command names a real channel (aplycs) actually defines. The ones that
# must NOT be collected matter more than the ones that must: !monitor and
# !headset both answer with a brand the mouse parser recognizes.
@pytest.mark.parametrize(
    "name", ["!sens", "!mouse", "!mousepad", "!dpi", "!mousesettings"]
)
def test_settings_commands_are_recognized(name):
    assert command_hint(name) is not None


@pytest.mark.parametrize(
    "name",
    ["!monitor", "!headset", "!keyboard", "!specs", "!res", "!rank", "!team",
     "!youtube", "!age", "!gear", "!setup", "nt",
     # Graphics settings, on every channel that defines it.
     "!settings"],
)
def test_unrelated_commands_are_not_collected(name):
    assert command_hint(name) is None


class TestRedactVariables:
    """Command text can hide an API key in a $(urlfetch) argument. The name
    of the substitution is all the parser needs; the arguments never reach
    the database."""

    def test_urlfetch_arguments_are_dropped(self):
        assert redact_variables(
            "$(urlfetch https://api.example.com/sens?key=SUPERSECRET)"
        ) == "$(urlfetch)"

    def test_surrounding_text_survives(self):
        assert redact_variables("@$(user) dpi is 1600") == "@$(user) dpi is 1600"

    def test_nested_parentheses_do_not_end_it_early(self):
        assert redact_variables(
            "a $(eval foo($(user), 2)) b"
        ) == "a $(eval) b"

    def test_text_without_variables_is_untouched(self):
        assert redact_variables("0.125 1600dpi") == "0.125 1600dpi"


class TestParseCommand:
    """Verbatim responses from aplycs's real Nightbot commands."""

    def test_sens_command_yields_both_numbers(self):
        parsed = parse_command("sens", "0.125 1600dpi")
        assert (parsed.dpi, parsed.sensitivity) == (1600, 0.125)

    def test_bare_model_name_needs_no_brand(self):
        # The chat parser returns None here: no keyword, no known brand.
        # The command name is what makes it a mouse.
        parsed = parse_command("mouse", "Viper V4 PRO")
        assert parsed.mouse_brand is None
        assert parsed.mouse_model == "Viper V4 PRO"

    def test_leading_brand_wins_over_a_later_one(self):
        parsed = parse_command("mousepad", "MEIY PULSAR GLASSPAD")
        assert parsed.pad_brand == "MEIY"
        assert parsed.pad_model == "PULSAR GLASSPAD"

    def test_known_brand_is_split_from_the_model(self):
        parsed = parse_command("mouse", "Razer Viper V3 Pro")
        assert (parsed.mouse_brand, parsed.mouse_model) == ("Razer", "Viper V3 Pro")

    def test_stock_phrasing_is_stripped(self):
        parsed = parse_command("mouse", "@$(user) I currently use the Lamzu Maya")
        assert (parsed.mouse_brand, parsed.mouse_model) == ("Lamzu", "Maya")

    @pytest.mark.parametrize(
        "message,brand,model",
        [
            # The chat parser caps a model at four words, which would drop
            # the "2" here. A command's whole message is the name.
            ("Logitech G Pro X Superlight 2", "Logitech", "G Pro X Superlight 2"),
            # The digit-run stop must not fire inside a model name.
            ("G303 Shroud Edition", None, "G303 Shroud Edition"),
            # A settings clause after the name is not part of the name.
            ("Razer Viper V3 Pro, 1600 dpi", "Razer", "Viper V3 Pro"),
            ("my mouse is the Lamzu Atlantis OG V2", "Lamzu", "Atlantis OG V2"),
            ("On my Razer Deathadder V3 Pro right now", "Razer", "Deathadder V3 Pro"),
            ("Vaxee XE Wireless", "Vaxee", "XE Wireless"),
        ],
    )
    def test_model_names_survive_intact(self, message, brand, model):
        parsed = parse_command("mouse", message)
        assert (parsed.mouse_brand, parsed.mouse_model) == (brand, model)

    def test_a_settings_clause_after_the_name_is_still_read(self):
        assert parse_command("mouse", "Razer Viper V3 Pro, 1600 dpi").dpi == 1600

    def test_dpi_command_with_a_bare_number(self):
        assert parse_command("dpi", "800").dpi == 800

    def test_unrelated_command_is_never_parsed(self):
        assert parse_command("monitor", "ZOWIE XL2566X+ (400Hz)") is None

    def test_pure_substitution_says_nothing(self):
        assert parse_command("mouse", "$(urlfetch)") is None

    def test_single_game_answer_is_read_normally(self):
        parsed = parse_command("sens", "Marvel Rivals: 0.35 @ 800 DPI")
        assert (parsed.dpi, parsed.sensitivity) == (800, 0.35)

    def test_multi_game_answer_without_rivals_is_not_guessed(self):
        # Verbatim from tarik's channel. Reading 1.5 here would file a
        # Counter-Strike sensitivity as a Marvel Rivals one.
        assert parse_command(
            "sens", "CSGO: 1.5 @ 800 DPI, VALORANT: .471 800 DPI"
        ) is None

    def test_multi_game_answer_isolates_the_rivals_clause(self):
        parsed = parse_command(
            "sens", "Valorant: 0.4 @ 1600 | Marvel Rivals: 0.85 @ 800"
        )
        assert (parsed.dpi, parsed.sensitivity) == (800, 0.85)

    def test_a_mouse_is_the_same_mouse_in_every_game(self):
        parsed = parse_command("mouse", "Razer Viper V3 Pro (valorant and cs2)")
        assert parsed.mouse_brand == "Razer"

    def test_prose_too_long_to_be_a_model_is_left_to_raw_text(self):
        assert parse_command(
            "mouse",
            "i have been using the same one for years and honestly i have no "
            "idea what it is called any more, ask in discord",
        ) is None
