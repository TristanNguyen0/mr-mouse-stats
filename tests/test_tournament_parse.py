import pytest

from mr_mouse_stats.liquipedia.tournament import parse_tournament


@pytest.fixture
def parsed(fixture_text):
    return parse_tournament(fixture_text("MR_Ignite_2026_Mid_Season_Finals.wikitext"))


def team(teams, name):
    return next(t for t in teams if t.name == name)


def person(entry, name):
    return next(p for p in entry.persons if p.name == name)


def test_tournament_meta(parsed):
    meta, _ = parsed
    assert meta.name == "Marvel Rivals Ignite 2026: Mid Season Finals"
    assert meta.series == "Marvel Rivals Ignite"
    assert meta.tier == "1"
    assert meta.start_date == "2026-07-29"
    assert meta.end_date == "2026-08-01"


def test_all_ten_teams_found_with_sections(parsed):
    _, teams = parsed
    assert len(teams) == 10
    sections = {t.name: t.section for t in teams}
    assert sections["Liquid Citadel"] == "Main Stage Teams"
    assert sections["100 Thieves"] == "Play-In Stage Teams"
    assert sum(t.section == "Main Stage Teams" for t in teams) == 6
    assert sum(t.section == "Play-In Stage Teams" for t in teams) == 4


def test_team_name_whitespace_stripped(parsed):
    _, teams = parsed
    assert any(t.name == "Swamp Gaming" for t in teams)


def test_starters_subs_and_staff(parsed):
    _, teams = parsed
    lc = team(teams, "Liquid Citadel")
    assert len(lc.persons) == 11
    starters = [p for p in lc.persons if not p.is_sub and not p.is_staff]
    assert [p.name for p in starters] == [
        "energy", "Shpeediry", "Polly", "Veswa", "nero", "cooper",
    ]
    assert person(lc, "SparkChief").is_sub
    assert person(lc, "Dinks").is_sub
    staff = [p.name for p in lc.persons if p.is_staff]
    assert staff == ["LegitRc", "Gator", "ArianWever"]
    assert person(lc, "ArianWever").role == "analyst"


def test_roles_normalized_to_lowercase(parsed):
    _, teams = parsed
    navi = team(teams, "Natus Vincere")
    assert person(navi, "Shuh").role == "coach"  # written |role=Coach on the page
    t100 = team(teams, "100 Thieves")
    assert person(t100, "iRemiix").role == "head coach"


def test_person_flag_override(parsed):
    _, teams = parsed
    kqz = person(team(teams, "Natus Vincere"), "Kqz")
    assert kqz.flag == "kw"
    assert kqz.is_sub


def test_played_false_and_loan_team(parsed):
    _, teams = parsed
    geng = team(teams, "gen.g esports")
    assert person(geng, "Salvation").played is False
    assert person(geng, "Salvation").is_sub
    assert person(geng, "Alx").loan_team == "Team Heretics"
    assert person(geng, "Bobo").played is None


def test_multi_word_player_names(parsed):
    _, teams = parsed
    swamp = team(teams, "Swamp Gaming")
    assert any(p.name == "Valentina Eve" for p in swamp.persons)
    aconyx = team(teams, "Aconyx")
    assert any(p.name == "Egg tart" for p in aconyx.persons)


def test_page_without_participants_returns_empty():
    meta, teams = parse_tournament("== Nothing here ==\nplain text")
    assert teams == []
    assert meta.name is None
