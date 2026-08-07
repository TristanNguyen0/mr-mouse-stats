"""Render the public stats site to a directory of static files.

Runs entirely from the local database — no network, no DB writes. The
output is self-contained HTML with inline SVG, hostable anywhere.
"""

from __future__ import annotations

import re
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape
from markupsafe import Markup

from .. import db
from . import queries, svg

_SLUG_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _slug(liquipedia_page: str) -> str:
    return _SLUG_UNSAFE.sub("_", liquipedia_page).strip("_")


def build_site(conn: db.Connection, out_dir: Path, generated_at: str) -> int:
    """Write the site into out_dir; returns the number of pages written."""
    env = Environment(
        loader=PackageLoader("mr_mouse_stats.site"),
        autoescape=select_autoescape(),
    )
    summaries = queries.player_summaries(conn)
    covered = [s for s in summaries if s.observations]
    slugs = {s.db_id: _slug(s.liquipedia_page) for s in covered}

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "players").mkdir(exist_ok=True)
    pages = 0

    def render(path: Path, template: str, root: str, **context: object) -> None:
        nonlocal pages
        html = env.get_template(template).render(
            root=root, generated_at=generated_at, **context
        )
        path.write_text(html)
        pages += 1

    render(
        out_dir / "index.html",
        "index.html",
        root="",
        page="index",
        total=len(summaries),
        covered=len(covered),
        dpi_chart=Markup(svg.bar_chart(queries.dpi_distribution(summaries))),
        edpi_chart=Markup(svg.bar_chart(queries.edpi_distribution(summaries))),
        mouse_chart=Markup(svg.bar_chart(queries.mouse_popularity(summaries))),
        roles=queries.role_comparison(summaries),
    )
    render(
        out_dir / "players.html",
        "players.html",
        root="",
        page="players",
        summaries=summaries,
        slugs=slugs,
    )
    for summary in covered:
        render(
            out_dir / "players" / f"{slugs[summary.db_id]}.html",
            "player.html",
            root="../",
            page="player",
            s=summary,
            history=queries.player_history(conn, summary.db_id),
        )
    return pages
