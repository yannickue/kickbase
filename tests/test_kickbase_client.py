import json
from pathlib import Path

import pytest
import responses

from kickbase_agent.kickbase_client import (
    KickbaseAuthError,
    KickbaseClient,
    KickbaseError,
    resolve_league_id,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@responses.activate
def test_login_success():
    responses.add(
        responses.POST,
        "https://api.kickbase.com/v4/user/login",
        json=_fixture("login.json"),
        status=200,
    )
    client = KickbaseClient()
    client.login("user@example.com", "secret")
    assert client.token == "mock-token-123"
    assert client.user_id == "mock-user-1"


@responses.activate
def test_login_missing_token_raises_auth_error():
    responses.add(
        responses.POST,
        "https://api.kickbase.com/v4/user/login",
        json={"something": "else"},
        status=200,
    )
    client = KickbaseClient()
    with pytest.raises(KickbaseAuthError):
        client.login("user@example.com", "wrong")


@responses.activate
def test_login_401_raises_auth_error():
    responses.add(
        responses.POST,
        "https://api.kickbase.com/v4/user/login",
        json={"error": "unauthorized"},
        status=401,
    )
    client = KickbaseClient()
    with pytest.raises(KickbaseAuthError):
        client.login("user@example.com", "wrong")


@responses.activate
def test_get_squad_parses_players():
    client = KickbaseClient()
    client.token = "fake"
    responses.add(
        responses.GET,
        "https://api.kickbase.com/v4/leagues/L1/squad",
        json=_fixture("squad.json"),
        status=200,
    )
    squad = client.get_squad("L1")
    assert len(squad) == 3
    kimmich = next(p for p in squad if "Kimmich" in p.name)
    assert kimmich.status == "verletzt"
    assert kimmich.status_code == 2
    assert kimmich.position == "Mittelfeld"
    assert kimmich.market_value == 21000000
    assert kimmich.market_value_change == -500000
    assert kimmich.team_id == "2"
    assert kimmich.team is None  # Team-Name wird separat via get_team_names aufgelöst


@responses.activate
def test_get_market_parses_offers_and_day():
    client = KickbaseClient()
    client.token = "fake"
    responses.add(
        responses.GET,
        "https://api.kickbase.com/v4/leagues/L1/market",
        json=_fixture("market.json"),
        status=200,
    )
    offers, day = client.get_market("L1")
    assert day == 24
    assert len(offers) == 2
    wirtz = next(o for o in offers if "Wirtz" in o.player.name)
    assert wirtz.price == 46500000
    assert wirtz.player.position == "Mittelfeld"


@responses.activate
def test_get_me_returns_budget_and_league_name():
    client = KickbaseClient()
    client.token = "fake"
    responses.add(
        responses.GET,
        "https://api.kickbase.com/v4/leagues/L1/me",
        json=_fixture("me.json"),
        status=200,
    )
    me = client.get_me("L1")
    assert me["budget"] == 4250000
    assert me["league_name"] == "Freunde-Liga"


@responses.activate
def test_get_ranking_parses_table():
    client = KickbaseClient()
    client.token = "fake"
    responses.add(
        responses.GET,
        "https://api.kickbase.com/v4/leagues/L1/ranking",
        json=_fixture("ranking.json"),
        status=200,
    )
    ranking = client.get_ranking("L1", 24)
    assert len(ranking) == 3
    leader = next(r for r in ranking if r["season_rank"] == 1)
    assert leader["name"] == "Rivale_Max"


@responses.activate
def test_get_team_names_builds_id_to_name_map():
    client = KickbaseClient()
    client.token = "fake"
    responses.add(
        responses.GET,
        "https://api.kickbase.com/v4/competitions/1/table",
        json=_fixture("team_table.json"),
        status=200,
    )
    names = client.get_team_names("1")
    assert names["2"] == "FC Bayern"


def test_resolve_league_id_uses_explicit_value():
    client = KickbaseClient()
    assert resolve_league_id(client, "explicit-id") == "explicit-id"


@responses.activate
def test_resolve_league_id_autodetects_single_league():
    client = KickbaseClient()
    client.token = "fake"
    responses.add(
        responses.GET,
        "https://api.kickbase.com/v4/leagues",
        json=_fixture("leagues.json"),
        status=200,
    )
    assert resolve_league_id(client, None) == "mock-league-1"


@responses.activate
def test_resolve_league_id_raises_on_multiple_leagues():
    client = KickbaseClient()
    client.token = "fake"
    responses.add(
        responses.GET,
        "https://api.kickbase.com/v4/leagues",
        json={"lins": [{"i": "a", "n": "Liga A"}, {"i": "b", "n": "Liga B"}]},
        status=200,
    )
    with pytest.raises(KickbaseError):
        resolve_league_id(client, None)
