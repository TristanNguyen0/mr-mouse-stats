"""Parse a tournament page's wikitext into metadata + team rosters.

Observed structure (MR_Ignite/2026/Mid_Season_Finals):

    {{TeamParticipants
    |{{Opponent|Team Name
     |players={{Persons
      |{{Person|role=dps|PlayerName|status=sub|type=staff|played=false|flag=xx}}
      ...}}
    }}
    ...}}

One {{TeamParticipants}} block per page section (Main Stage / Play-In).
"""

from __future__ import annotations

import logging

import mwparserfromhell
from mwparserfromhell.nodes import Heading, Template

from ..models import RosterPerson, TeamEntry, TournamentMeta

logger = logging.getLogger(__name__)


def _tname(template: Template) -> str:
    return str(template.name).strip().lower().replace("_", " ")


def _get(template: Template, param: str) -> str | None:
    if not template.has(param, ignore_empty=True):
        return None
    return str(template.get(param).value.strip_code()).strip() or None


def parse_tournament(wikitext: str) -> tuple[TournamentMeta, list[TeamEntry]]:
    code = mwparserfromhell.parse(wikitext)
    meta = _parse_infobox(code)
    teams: list[TeamEntry] = []
    section: str | None = None
    for node in code.nodes:
        if isinstance(node, Heading):
            section = str(node.title.strip_code()).strip()
        elif isinstance(node, Template) and _tname(node) == "teamparticipants":
            teams.extend(_parse_participants(node, section))
    if not teams:
        logger.warning("no TeamParticipants blocks found on page")
    return meta, teams


def _parse_infobox(code: mwparserfromhell.wikicode.Wikicode) -> TournamentMeta:
    for template in code.filter_templates():
        if _tname(template) == "infobox league":
            return TournamentMeta(
                name=_get(template, "name"),
                series=_get(template, "series"),
                tier=_get(template, "liquipediatier"),
                start_date=_get(template, "sdate"),
                end_date=_get(template, "edate"),
            )
    return TournamentMeta(None, None, None, None, None)


def _parse_participants(block: Template, section: str | None) -> list[TeamEntry]:
    teams = []
    for param in block.params:
        if param.showkey:  # named params like |showplayerinfo= are not teams
            continue
        for opponent in param.value.filter_templates(recursive=False):
            if _tname(opponent) != "opponent":
                continue
            name = _get(opponent, "1")
            if not name:
                logger.warning("Opponent template without a team name; skipped")
                continue
            teams.append(
                TeamEntry(
                    name=name,
                    section=section,
                    persons=tuple(_parse_persons(opponent)),
                )
            )
    return teams


def _parse_persons(opponent: Template) -> list[RosterPerson]:
    persons: list[RosterPerson] = []
    if not opponent.has("players", ignore_empty=True):
        logger.warning(
            "Opponent has no players param",
            extra={"fields": {"team": _get(opponent, "1")}},
        )
        return persons
    for template in opponent.get("players").value.filter_templates(recursive=True):
        if _tname(template) != "person":
            continue
        name = _get(template, "1")
        if not name:
            continue
        role = _get(template, "role")
        played_raw = _get(template, "played")
        persons.append(
            RosterPerson(
                name=name,
                role=role.lower() if role else None,
                is_sub=(_get(template, "status") or "").lower() == "sub",
                is_staff=(_get(template, "type") or "").lower() == "staff",
                played=False if played_raw and played_raw.lower() == "false" else None,
                flag=_get(template, "flag"),
                loan_team=_get(template, "team"),
            )
        )
    return persons
