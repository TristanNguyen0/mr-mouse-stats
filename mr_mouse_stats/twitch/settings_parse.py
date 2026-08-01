"""Parse free-text settings responses into structured fields.

These patterns are provisional, seeded from common chatbot response
formats. Raw candidates are stored verbatim in twitch_messages, so as the
patterns improve, `parse-observations` can re-run over history — parsing
failures lose nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Longest names first so "Endgame Gear" wins over any shorter overlap.
MOUSE_BRANDS = sorted(
    [
        "Endgame Gear", "Finalmouse", "SteelSeries", "G-Wolves", "Ninjutso",
        "Logitech", "Glorious", "Waizowl", "WLmouse", "Hitscan", "Fantech",
        "Corsair", "HyperX", "Pulsar", "Scyrox", "Lamzu", "Razer", "Vaxee",
        "Xtrfy", "Zowie", "BenQ", "ATK", "VXE", "VGN",
    ],
    key=len,
    reverse=True,
)

_DPI = re.compile(r"\b(\d{3,5})\s*dpi\b|\bdpi\b[:\s@|>-]*(\d{3,5})\b", re.I)
_WINDOWS = re.compile(r"\bwin(?:dows)?(?:\s*sens)?\b[:\s@|>-]*(\d{1,2})\b", re.I)
_SENS = re.compile(
    r"\b(?:in-?game\s*)?sens(?:itivity)?\b[:\s@|>-]*(\d+(?:\.\d+)?)\b"
    r"|\b(\d+(?:\.\d+)?)\s*(?:in-?game\s*)?sens(?:itivity)?\b",
    re.I,
)
_BRAND = re.compile(
    r"\b(" + "|".join(re.escape(b) for b in MOUSE_BRANDS) + r")\b", re.I
)
_MODEL_STOP = re.compile(r"[,;|]|\bdpi\b|\bsens\b|\bwin\b|\d{3,5}\b", re.I)
# prose words that mark the end of a model name, and a length cap: model
# names are short ("Starlight Pro TenZ", "G Pro X Superlight")
_MODEL_PROSE_WORDS = frozenset({
    "right", "now", "currently", "atm", "and", "with", "but", "he", "she",
    "they", "i", "on", "at", "for", "since", "is", "was", "the",
})
_MODEL_MAX_WORDS = 4


@dataclass(frozen=True)
class ParsedSettings:
    dpi: int | None = None
    sensitivity: float | None = None
    windows_sens: int | None = None
    mouse_brand: str | None = None
    mouse_model: str | None = None


def _canonical_brand(matched: str) -> str:
    for brand in MOUSE_BRANDS:
        if brand.lower() == matched.lower():
            return brand
    return matched


def parse_settings(text: str) -> ParsedSettings | None:
    """Extract settings from a chat response; None if nothing recognizable."""
    working = text

    windows_sens = None
    match = _WINDOWS.search(working)
    if match:
        windows_sens = int(match.group(1))
        working = working[: match.start()] + working[match.end() :]

    dpi = None
    match = _DPI.search(working)
    if match:
        dpi = int(match.group(1) or match.group(2))
        working = working[: match.start()] + working[match.end() :]

    sensitivity = None
    match = _SENS.search(working)
    if match:
        sensitivity = float(match.group(1) or match.group(2))

    mouse_brand = mouse_model = None
    match = _BRAND.search(text)
    if match:
        mouse_brand = _canonical_brand(match.group(1))
        tail = text[match.end() :].lstrip(" :-")
        stop = _MODEL_STOP.search(tail)
        model = (tail[: stop.start()] if stop else tail).strip(" .!?:-")
        words = []
        for word in model.split():
            if word.lower() in _MODEL_PROSE_WORDS or len(words) == _MODEL_MAX_WORDS:
                break
            words.append(word)
        mouse_model = " ".join(words) or None

    if dpi is None and sensitivity is None and windows_sens is None and mouse_brand is None:
        return None
    return ParsedSettings(
        dpi=dpi,
        sensitivity=sensitivity,
        windows_sens=windows_sens,
        mouse_brand=mouse_brand,
        mouse_model=mouse_model,
    )
