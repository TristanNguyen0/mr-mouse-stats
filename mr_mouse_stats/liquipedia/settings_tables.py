"""Parse {{Mouse settings table}} templates from player pages.

Observed variants (real pages): optional |ref= citation, |polling=,
|zoom=, |windows=, pad params, empty |desc=. A page may carry several
tables over time — all are returned, oldest text-order first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import mwparserfromhell
from mwparserfromhell.nodes import Template

_REF_URL = re.compile(r"\[(https?://\S+)")


@dataclass(frozen=True)
class MouseSettingsEntry:
    date: str | None = None
    brand: str | None = None
    model: str | None = None
    dpi: int | None = None
    sensitivity: float | None = None
    windows: int | None = None
    polling: int | None = None
    zoom: float | None = None
    pad_brand: str | None = None
    pad_model: str | None = None
    ref_url: str | None = None


def _text(template: Template, param: str) -> str | None:
    if not template.has(param, ignore_empty=True):
        return None
    return str(template.get(param).value.strip_code()).strip() or None


def _number(template: Template, param: str, cast) -> int | float | None:
    raw = _text(template, param)
    if raw is None:
        return None
    try:
        return cast(raw)
    except ValueError:
        return None


def _ref_url(template: Template) -> str | None:
    if not template.has("ref", ignore_empty=True):
        return None
    match = _REF_URL.search(str(template.get("ref").value))
    return match.group(1) if match else None


def parse_mouse_settings(wikitext: str) -> list[MouseSettingsEntry]:
    entries = []
    for template in mwparserfromhell.parse(wikitext).filter_templates():
        if str(template.name).strip().lower() != "mouse settings table":
            continue
        entries.append(
            MouseSettingsEntry(
                date=_text(template, "date"),
                brand=_text(template, "brand"),
                model=_text(template, "model"),
                dpi=_number(template, "dpi", int),
                sensitivity=_number(template, "sensitivity", float),
                windows=_number(template, "windows", int),
                polling=_number(template, "polling", int),
                zoom=_number(template, "zoom", float),
                pad_brand=_text(template, "pad-brand"),
                pad_model=_text(template, "pad-model"),
                ref_url=_ref_url(template),
            )
        )
    return entries
