"""Frozen dataclasses shared between parsers, DAL, and CLI."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class WikiPage:
    """One page as returned by the MediaWiki API (after normalization/redirects)."""

    title: str
    wikitext: str | None
    missing: bool = False


@dataclass(frozen=True)
class TournamentMeta:
    name: str | None
    series: str | None
    tier: str | None
    start_date: str | None
    end_date: str | None


@dataclass(frozen=True)
class RosterPerson:
    name: str
    role: str | None
    is_sub: bool = False
    is_staff: bool = False
    played: bool | None = None
    flag: str | None = None
    loan_team: str | None = None


@dataclass(frozen=True)
class TeamEntry:
    name: str
    section: str | None
    persons: tuple[RosterPerson, ...] = ()


@dataclass(frozen=True)
class SocialAccount:
    platform: str
    handle: str
    url: str | None = None


@dataclass(frozen=True)
class PlayerInfo:
    page_title: str
    player_id: str | None
    real_name: str | None = None
    romanized_name: str | None = None
    country: str | None = None
    roles: str | None = None
    socials: tuple[SocialAccount, ...] = ()
