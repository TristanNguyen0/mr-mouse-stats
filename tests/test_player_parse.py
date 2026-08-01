from mr_mouse_stats.liquipedia.player import parse_player


def socials_by_platform(info):
    out = {}
    for account in info.socials:
        out.setdefault(account.platform, []).append(account)
    return out


def test_basic_player_with_twitch(fixture_text):
    info = parse_player("Energy", fixture_text("player_Energy.wikitext"))
    assert info.player_id == "energy"
    assert info.real_name == "Jovan"
    assert info.country == "United States"
    assert info.roles == "Duelist"
    socials = socials_by_platform(info)
    assert socials["twitch"][0].handle == "energy"
    assert socials["twitch"][0].url == "https://www.twitch.tv/energy"
    assert socials["twitter"][0].handle == "enwrgyy"


def test_player_without_twitch_has_chinese_platforms(fixture_text):
    info = parse_player("TAROCOOK1E", fixture_text("player_TAROCOOK1E.wikitext"))
    socials = socials_by_platform(info)
    assert "twitch" not in socials
    assert socials["bilibili"][0].handle == "3546635086858552"
    assert socials["bilibili"][0].url == "https://space.bilibili.com/3546635086858552"
    assert socials["huya"][0].handle == "17916746"
    assert info.real_name == "钟云龙"
    assert info.romanized_name == "Zhong Yunlong"


def test_player_with_many_socials(fixture_text):
    info = parse_player("Fate", fixture_text("player_Fate.wikitext"))
    socials = socials_by_platform(info)
    assert socials["twitch"][0].handle == "fatemr_"
    assert socials["youtube"][0].handle == "@fate.rivals"
    assert socials["youtube"][0].url == "https://www.youtube.com/@fate.rivals"
    assert socials["faceit"][0].handle == "fate_ow"
    assert socials["tiktok"][0].url == "https://www.tiktok.com/@fategb"
    assert socials["discord"][0].handle == "SkAn4v8Nzs"


def test_youtube_channel_id_form(fixture_text):
    info = parse_player("Jur3ky", fixture_text("player_Jur3ky.wikitext"))
    socials = socials_by_platform(info)
    assert (
        socials["youtube"][0].url
        == "https://www.youtube.com/channel/UCyLD9rO6FaCcTku_loFwhbw"
    )


def test_empty_social_params_ignored(fixture_text):
    # Energy's infobox has |image= empty; no empty-handle accounts may appear
    info = parse_player("Energy", fixture_text("player_Energy.wikitext"))
    assert all(account.handle for account in info.socials)


def test_disambiguation_page_returns_none(fixture_text):
    info = parse_player("Ghost", fixture_text("player_Ghost_disambiguation.wikitext"))
    assert info is None


def test_non_player_wikitext_returns_none():
    assert parse_player("Whatever", "Just some '''article''' text.") is None
