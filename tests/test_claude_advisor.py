from kickbase_agent.claude_advisor import KickbaseSnapshot, build_user_prompt
from kickbase_agent.kickbase_client import MarketOffer, Player


def make_snapshot() -> KickbaseSnapshot:
    squad = [
        Player(
            id="p1",
            name="Joshua Kimmich",
            position="MF",
            team="FC Bayern",
            market_value=21_000_000,
            market_value_trend="up",
            points=210,
            status="verletzt",
        )
    ]
    market = [
        MarketOffer(
            player=Player(
                id="p4",
                name="Florian Wirtz",
                position="MF",
                team="Leverkusen",
                market_value=45_000_000,
                market_value_trend="up",
                points=240,
                status="fit",
            ),
            price=46_500_000,
            expires_at="2026-09-06T18:00:00Z",
            seller=None,
        )
    ]
    return KickbaseSnapshot(
        league_name="Freunde-Liga",
        budget=4_250_000,
        team_value=38_750_000,
        placement=3,
        squad=squad,
        market=market,
        table=[{"team_name": "Test Manager", "rank": 3, "points": 812}],
    )


def test_build_user_prompt_includes_key_facts():
    prompt = build_user_prompt(make_snapshot())
    assert "Freunde-Liga" in prompt
    assert "Kimmich" in prompt
    assert "verletzt" in prompt
    assert "Wirtz" in prompt
    assert "46.500.000" in prompt or "46500000" in prompt
    assert "4.250.000" in prompt or "4250000" in prompt


def test_build_user_prompt_handles_missing_data():
    snapshot = KickbaseSnapshot(
        league_name="Leere Liga",
        budget=None,
        team_value=None,
        placement=None,
        squad=[],
        market=[],
        table=[],
    )
    prompt = build_user_prompt(snapshot)
    assert "unbekannt" in prompt
    assert "keine Kaderdaten verfügbar" in prompt
    assert "keine Marktdaten verfügbar" in prompt
    assert "keine Tabellendaten verfügbar" in prompt
