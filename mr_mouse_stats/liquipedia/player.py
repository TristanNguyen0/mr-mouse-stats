"""Parse a player page's {{Infobox player}} into identity + social accounts.

Not every roster name resolves to a player page: the page may be missing, a
disambiguation page, or something else entirely (names like "Ghost", "Self").
``parse_player`` returns None when the page has no {{Infobox player}} —
callers record that as a resolution failure rather than guessing.
"""

from __future__ import annotations

import logging
import re

import mwparserfromhell

from ..models import PlayerInfo, SocialAccount

logger = logging.getLogger(__name__)

# Infobox param name -> URL template. Handles change; URLs are best-effort
# conveniences, the (platform, handle) pair is the datum.
SOCIAL_URLS = {
    "twitch": "https://www.twitch.tv/{}",
    "twitter": "https://x.com/{}",
    "youtube": None,  # special-cased: value may be @handle or channel/<id>
    "instagram": "https://www.instagram.com/{}",
    "tiktok": "https://www.tiktok.com/@{}",
    "facebook": "https://www.facebook.com/{}",
    "discord": "https://discord.gg/{}",
    "faceit": "https://www.faceit.com/en/players/{}",
    "bilibili": "https://space.bilibili.com/{}",
    "huya": "https://www.huya.com/{}",
    "douyu": "https://www.douyu.com/{}",
    "vk": "https://vk.com/{}",
    "weibo": "https://weibo.com/{}",
}

_SOCIAL_PARAM = re.compile(r"^([a-z]+?)(\d*)$")


def _youtube_url(handle: str) -> str:
    if handle.startswith("@") or "/" in handle:
        return f"https://www.youtube.com/{handle}"
    return f"https://www.youtube.com/@{handle}"


def parse_player(page_title: str, wikitext: str) -> PlayerInfo | None:
    code = mwparserfromhell.parse(wikitext)
    infobox = None
    for template in code.filter_templates():
        if str(template.name).strip().lower() == "infobox player":
            infobox = template
            break
    if infobox is None:
        logger.info(
            "page has no Infobox player",
            extra={"fields": {"page": page_title}},
        )
        return None

    fields: dict[str, str] = {}
    socials: list[SocialAccount] = []
    for param in infobox.params:
        key = str(param.name).strip().lower()
        value = str(param.value.strip_code()).strip()
        if not value:
            continue
        match = _SOCIAL_PARAM.match(key)
        if match and match.group(1) in SOCIAL_URLS:
            platform = match.group(1)
            url = (
                _youtube_url(value)
                if platform == "youtube"
                else SOCIAL_URLS[platform].format(value)
            )
            socials.append(SocialAccount(platform=platform, handle=value, url=url))
        else:
            fields[key] = value

    return PlayerInfo(
        page_title=page_title,
        player_id=fields.get("id"),
        real_name=fields.get("name"),
        romanized_name=fields.get("romanized_name"),
        country=fields.get("country"),
        roles=fields.get("roles") or fields.get("role"),
        socials=tuple(socials),
    )
