"""Turn a bot command definition into structured settings.

A chat response has to be parsed blind, so twitch.settings_parse looks for
keywords ("1600 dpi", "sens 0.35"). A command definition arrives with its
name attached, and the name says which field the text holds: whatever
`!mouse` answers is a mouse, even when the text is the bare "Viper V4 PRO"
with no brand and no keyword for the chat parser to anchor on.

So this module keeps twitch.settings_parse as the first pass and uses the
command name only as a fallback, to interpret text that pass could not.
"""

from __future__ import annotations

import re
from dataclasses import replace

from ..twitch.settings_parse import (
    MODEL_PROSE_WORDS,
    MOUSE_BRANDS,
    ParsedSettings,
    canonical_brand,
    parse_settings,
)

# Command name (lowercased, leading '!' stripped) -> which field it holds.
#
# Deliberately tight, and it is the only filter: a name absent from here is
# never fetched into the database at all. It has to be tight, because a
# streamer's other commands are full of hardware that is not a mouse — a
# real channel answers !monitor with "ZOWIE XL2566X+" and !headset with
# "Razer Blackshark V3 Pro", both of which name a brand the mouse parser
# recognizes. Excluding those command names is what stops a monitor being
# recorded as someone's mouse.
#
# !gear / !setup / !specs are out for the same reason: they list a whole
# battlestation, and attributing any one brand in them to the mouse is a
# guess. The dedicated commands below are where the data actually is.
COMMAND_HINTS: dict[str, str] = {
    "dpi": "dpi",
    "edpi": "dpi",
    "sens": "sens",
    "sensitivity": "sens",
    "sensi": "sens",
    "mouse": "mouse",
    "mice": "mouse",
    # Only the one that says "mouse". A bare !settings is graphics settings
    # in practice — across the Ignite roster every single one answered with
    # things like "all low, textures medium & nvidia dlss ultra performance"
    # or a link to a settings clip, never a sensitivity. Collecting those
    # buys nothing and risks reading a stray number out of a clip URL.
    "mousesettings": "sens",
    "mousepad": "pad",
    "pad": "pad",
    "mousemat": "pad",
    "mat": "pad",
    "padsize": "pad",
}

PAD_BRANDS = sorted(
    [
        "Lethal Gaming Gear", "Wallhack", "X-raypad", "Artisan", "Zowie",
        "Pulsar", "Glorious", "Logitech", "SteelSeries", "Endgame Gear",
        "Skypad", "Aqua Control", "Cyberpuck", "Odin", "MEIY", "LGG",
        "Vaxee", "Razer", "Corsair", "Vancer", "Superglide",
    ],
    key=len,
    reverse=True,
)

# Nightbot substitutes these at send time. Their *arguments* are the
# problem: $(urlfetch https://host/path?key=SECRET) is a documented way to
# hide an API key in a command, and it is the reason Nightbot does not
# publish this endpoint. Strip arguments before anything is persisted.
_VARIABLE = re.compile(r"\$\(\s*([a-z_]+)")

# Leading noise a command response usually opens with: a mention of the
# asker, then a stock phrase.
_LEADING_MENTION = re.compile(r"^\s*[@/]?\s*(?:\$\(\w+\)|@\S+)[\s,:-]*", re.I)
_LEADING_PHRASE = re.compile(
    r"^\s*(?:(?:my|his|her|their|the)\s+)?"
    r"(?:current(?:ly)?\s+)?"
    r"(?:(?:in-?game\s+)?(?:mouse|mousepad|pad|sens(?:itivity)?|dpi|settings?)\s+)*"
    r"(?:is|are|=|:|uses?|using|rocking|playing\s+(?:on|with)|on|with)?[\s:=-]*",
    re.I,
)
_TRAILING_NOISE = re.compile(r"[\s.!?,:;|-]+$")
# Where a product name ends and a settings clause begins, for "Razer Viper,
# 1600 dpi". The same stops the chat parser uses, minus its four-word cap
# (right for prose, wrong when the whole message is the name) and with the
# digit run anchored so it cannot fire inside a model like "G303".
_NAME_STOP = re.compile(
    r"[,;|]|\bdpi\b|\bsens(?:itivity)?\b|\bwin(?:dows)?\b"
    r"|(?<![A-Za-z0-9])\d{3,5}\b",
    re.I,
)

# Pros play more than one game, and one !sens command often answers for all
# of them: a real channel replies "CSGO: 1.5 @ 800 DPI, VALORANT: .471 800
# DPI". Taking the first number there records a Counter-Strike sensitivity
# as a Marvel Rivals one. So when several games are named, either the
# Marvel Rivals clause is isolated or nothing is read at all.
#
# Only unambiguous names: "val", "ow" and "cod" appear inside model names.
_GAME = re.compile(
    r"\b(marvel\s*rivals|rivals|valorant|counter-?strike|cs\s*:?\s*go|cs2|csgo|"
    r"overwatch|apex(?:\s*legends)?|fortnite|rainbow\s*six|r6|siege|warzone|"
    r"pubg|deadlock|the\s*finals|quake|splitgate|aimlab|aim\s*lab)\b",
    re.I,
)
_RIVALS = re.compile(r"\b(marvel\s*rivals|rivals)\b", re.I)


def _game_segment(text: str) -> str | None:
    """The part of a multi-game answer that speaks for Marvel Rivals.

    Returns the text unchanged when only one game (or none) is named, the
    Marvel Rivals clause when several are, and None when several are named
    and Marvel Rivals is not among them — an answer about other games is
    not a Marvel Rivals observation, and the raw text stays on file.
    """
    matches = list(_GAME.finditer(text))
    if len({m.group(0).lower() for m in matches}) < 2:
        return text
    for index, match in enumerate(matches):
        if _RIVALS.fullmatch(match.group(0)):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            return text[match.end() : end]
    return None
# A model name is short. Anything longer is prose that happens to live in a
# !mouse command, and belongs in raw_text only.
_MAX_NAME_CHARS = 60


def normalize_name(name: str) -> str:
    """'!Sens ' -> 'sens'."""
    return name.strip().lstrip("!").strip().lower()


def command_hint(name: str) -> str | None:
    """Which settings field a command name promises, or None if it is not
    a settings command at all."""
    return COMMAND_HINTS.get(normalize_name(name))


def is_settings_command(name: str) -> bool:
    return command_hint(name) is not None


def redact_variables(message: str) -> str:
    """Drop the arguments of every $(...) substitution, keeping its name.

    Applied before the text reaches the database, not after: the arguments
    can carry API keys, and the shape ("$(urlfetch)") is all the parser
    ever needed from them.
    """
    out: list[str] = []
    i = 0
    while i < len(message):
        match = _VARIABLE.search(message, i)
        if match is None:
            out.append(message[i:])
            break
        out.append(message[i : match.start()])
        out.append(f"$({match.group(1)})")
        # Skip to the matching close paren, counting nesting so that a
        # nested $(...) inside the arguments does not end it early.
        depth = 1
        j = match.end()
        while j < len(message) and depth:
            if message[j] == "(":
                depth += 1
            elif message[j] == ")":
                depth -= 1
            j += 1
        i = j
    return "".join(out)


def _clean_name(text: str) -> str | None:
    """Reduce a command response to the bare product name it carries."""
    cleaned = _LEADING_MENTION.sub("", text)
    cleaned = _LEADING_PHRASE.sub("", cleaned)
    stop = _NAME_STOP.search(cleaned)
    if stop:
        cleaned = cleaned[: stop.start()]
    cleaned = _TRAILING_NOISE.sub("", cleaned).strip(" \"'")
    if not cleaned or len(cleaned) > _MAX_NAME_CHARS:
        return None
    # A response that is nothing but a substitution says nothing.
    if re.fullmatch(r"(?:\$\(\w+\)\s*)+", cleaned):
        return None
    return cleaned


_DETERMINERS = frozenset({"the", "a", "an", "my", "his", "her", "their", "our"})


def _trim_prose(model: str) -> str | None:
    """Drop the sentence a model name is sitting in.

    Leading determiners go first, then everything from the first prose word
    on, so "my Deathadder V3 Pro right now" leaves "Deathadder V3 Pro".
    Determiners are stripped before the prose scan because "the" is itself
    a prose word, and cutting at it would leave nothing.
    """
    words = model.split()
    while words and words[0].lower().strip(".,") in _DETERMINERS:
        words.pop(0)
    kept: list[str] = []
    for word in words:
        if word.lower().strip(".,") in MODEL_PROSE_WORDS:
            break
        kept.append(word)
    return " ".join(kept) or None


def _split_brand(text: str, brands: list[str]) -> tuple[str | None, str | None]:
    """Split a product name into (brand, model). Either half may be None:
    'Razer' alone is a brand, 'Viper V4 Pro' alone is a model.

    Earliest match wins, longest breaking the tie. Product names lead with
    the maker, so when two known brands both appear — "MEIY Pulsar
    Glasspad" names two pad makers — the leading one is the brand and the
    rest is the model.
    """
    matches = []
    for brand in brands:
        match = re.search(rf"\b{re.escape(brand)}\b", text, re.I)
        if match:
            matches.append((match.start(), -len(brand), match))
    if not matches:
        return None, _trim_prose(text)
    match = min(matches)[2]
    # The model follows the brand ("Lamzu Maya"); whatever precedes it is
    # almost always the sentence the name arrived in ("i currently use the
    # Lamzu Maya"), so it is only a fallback.
    after = _trim_prose(text[match.end() :].strip(" -:,"))
    before = _trim_prose(text[: match.start()].strip(" -:,"))
    return canonical_brand(match.group(0), brands), after or before


def parse_command(name: str, message: str) -> ParsedSettings | None:
    """Structured settings from one command definition, or None.

    The message is expected to have been through redact_variables already
    (fetch does it before persisting); running it again is harmless.
    """
    hint = command_hint(name)
    if hint is None:
        return None
    text = redact_variables(message)

    if hint == "pad":
        brand, model = _split_brand(_clean_name(text) or "", PAD_BRANDS)
        if brand is None and model is None:
            return None
        return ParsedSettings(pad_brand=brand, pad_model=model)

    if hint in ("dpi", "sens"):
        # A mouse is a mouse in every game, but a sensitivity is not.
        segment = _game_segment(text)
        if segment is None:
            return None
        text = segment

    parsed = parse_settings(text)
    if hint == "mouse":
        # The name comes from here, not from the chat parser, for two
        # reasons: that parser needs a known brand to see a mouse at all
        # (the command name has already established there is one), and it
        # caps a model at four words — right for prose, wrong when the
        # whole message is the name, as in "Logitech G Pro X Superlight 2".
        # Any dpi or sens it did find is kept.
        name_text = _clean_name(text)
        if name_text:
            brand, model = _split_brand(name_text, MOUSE_BRANDS)
            if brand is not None or model is not None:
                return replace(
                    parsed or ParsedSettings(),
                    mouse_brand=brand,
                    mouse_model=model,
                )
    return parsed
