"""Canonical device names for the usage ranking.

Players name the same mouse a dozen ways — "Logitech G PRO X Superlight",
"gpx superlight", "gpro superlight" and "gpw superlight" are one device, and
counted verbatim they are four rows of one. This maps a free-text mouse name
onto a canonical "Brand Model", so the ranking counts devices instead of
spellings.

Presentation only: `settings_observations` keeps the raw text forever, the
player pages show what the player actually said, and nothing here writes.
An unrecognised name is title-cased and kept as its own entry rather than
guessed at — a wrong merge is worse than a long tail.
"""

from __future__ import annotations

import re

# (canonical name, substrings that identify it). Matched against the raw name
# lowercased with punctuation flattened, most specific FIRST: "superlight 2"
# has to beat "superlight", and "viper v3 pro" has to beat "viper v3".
_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    # Logitech
    (
        "Logitech G Pro X Superlight 2",
        ("g pro x superlight 2", "gpx superlight 2", "gpro superlight 2",
         "g pro superlight 2", "superlight 2"),
    ),
    (
        "Logitech G Pro X2 Superstrike",
        ("g pro x2 superstrike", "g pro x superstrike", "pro x2 superstrike",
         "x2 superstrike", "superstrike"),
    ),
    (
        "Logitech G Pro X Superlight",
        ("g pro x superlight", "gpx superlight", "gpro superlight",
         "gpw superlight", "g pro superlight", "superlight"),
    ),
    ("Logitech G Pro Wireless", ("g pro wireless", "gpw")),
    ("Logitech G502 X", ("g502 x", "g502")),
    # Razer
    ("Razer Viper V4 Pro", ("viper v4 pro", "viper v4")),
    ("Razer Viper V3 Pro", ("viper v3 pro",)),
    ("Razer Viper V3", ("viper v3",)),
    ("Razer Viper V2 Pro", ("viper v2 pro", "viper v2")),
    ("Razer DeathAdder V3 Pro", ("deathadder v3 pro", "deathadder v3")),
    # Finalmouse
    ("Finalmouse ULX", ("ulx",)),
    ("Finalmouse Frostlord", ("frostlord",)),
    ("Finalmouse Starlight Pro", ("starlight pro", "starlight")),
    # Everyone else
    ("Pulsar X2", ("pulsar x2", "x2 cl", "x2v2", "x2 v2")),
    ("Vaxee Zygen NP01S", ("zygen np01s", "np01s", "zygen")),
    ("Ninjutso Sora", ("sora",)),
    ("Lamzu Maya", ("maya",)),
    ("Lamzu Atlantis", ("atlantis",)),
    ("Endgame Gear OP1", ("op1",)),
    ("Xtrfy M8", ("xtrfy m8", "m8")),
    ("Zowie EC2", ("ec2",)),
    ("Zowie ZA13", ("za13",)),
)

# Names that reach the mouse columns but are not mice. The parser reads a
# `!mouse` answer that lists the whole desk ("G640 x NAVI" is a mousepad), so
# a mouse-only ranking has to drop them.
_NOT_A_MOUSE = ("g640", "g440", "artisan", "mousepad", "deskmat", "keyboard")

_PUNCTUATION = re.compile(r"[^a-z0-9]+")


def _flatten(raw: str) -> str:
    return _PUNCTUATION.sub(" ", raw.lower()).strip()


def canonical_mouse(raw: str | None) -> str | None:
    """The canonical name for a free-text mouse, or None if it isn't a mouse.

    Unrecognised names come back tidied but otherwise intact, so a device we
    have no alias for still appears in the ranking under one spelling.
    """
    if not raw:
        return None
    flat = _flatten(raw)
    if not flat:
        return None
    if any(term in flat for term in _NOT_A_MOUSE):
        return None
    for canonical, patterns in _ALIASES:
        if any(pattern in flat for pattern in patterns):
            return canonical
    return " ".join(word.capitalize() for word in flat.split())
