from kickbase_agent.analytics import (
    analyze_market_premiums,
    analyze_own_transfers,
    analyze_rival_needs,
    compute_player_signals,
)
from kickbase_agent.kickbase_client import MarketOffer, Player


def _player(**kwargs):
    base = dict(
        id="p1",
        name="Test",
        position="Mittelfeld",
        position_code=3,
        team_id="1",
        team="Bayern",
        market_value=10_000_000,
        market_value_change=0,
        points=100,
        average_points=50,
        status="fit",
        status_code=0,
    )
    base.update(kwargs)
    return Player(**base)


def test_minutes_and_points_per_90_are_computed():
    detail = {
        "i": "1",
        "fn": "Max",
        "ln": "Muster",
        "tp": 180,
        "sec": 10800,  # 180 Minuten
        "ph": [{"hp": True, "p": 90}, {"hp": True, "p": 90}],
    }
    sig = compute_player_signals(detail)
    assert sig.matches_played == 2
    assert sig.matchdays == 2
    assert sig.minutes_total == 180
    assert sig.minutes_per_matchday == 90
    assert sig.points_per_90 == 90  # 180 Punkte auf 180 Minuten


def test_small_sample_is_shrunk_towards_prior_season():
    """Ein Ausreißer aus einem Kurzeinsatz darf die Prognose nicht dominieren."""
    detail = {
        "i": "1",
        "ln": "Joker",
        "tp": 40,
        "sec": 300,  # 5 Minuten -> 720 Punkte/90 wären reines Rauschen
        "ph": [{"hp": True, "p": 40}],
    }
    performance = [
        {"ti": "2025/2026", "ph": [{"p": 60, "mp": "90'"} for _ in range(30)]},
        {"ti": "2026/2027", "ph": [{"p": 40, "mp": "5'", "cur": True}]},
    ]
    sig = compute_player_signals(detail, performance=performance)
    assert sig.points_per_90 > 700  # Rohwert ist absurd hoch
    assert sig.prior_points_per_90 == 60
    # Geschrumpfte Erwartung liegt deutlich näher am Prior als am Rohwert.
    assert 60 < sig.expected_points_per_90 < 250
    assert sig.sample_warning is not None


def test_market_value_series_ignores_zero_padding():
    """Führende Nullen heißen 'noch nicht gelistet', nicht 'Marktwert null'."""
    history = {
        "it": [{"dt": i, "mv": 0} for i in range(80)]
        + [{"dt": 80 + i, "mv": 5_000_000 + i * 10_000} for i in range(12)],
        "lmv": 5_000_000,
        "hmv": 5_110_000,
    }
    sig = compute_player_signals({"i": "1", "ln": "Neu"}, mv_history=history)
    assert sig.mv_history_days == 12
    assert sig.mv_change_7d == 70_000  # 7 Tage * 10.000
    assert sig.mv_change_30d is None  # zu kurze Reihe für 30 Tage


def test_market_premiums_only_count_manager_offers():
    offers = [
        MarketOffer(player=_player(market_value=10_000_000), price=11_000_000,
                    expires_in_seconds=None, seller="Rivale"),
        MarketOffer(player=_player(market_value=10_000_000), price=10_000_000,
                    expires_in_seconds=3600, seller=None),  # Kickbase selbst
    ]
    stats = analyze_market_premiums(offers)
    assert stats.count == 1
    assert abs(stats.median_premium - 0.10) < 1e-9


def test_own_transfers_pair_buys_with_sells():
    transfers = [
        {"pi": "7", "pn": "Flop", "tty": 1, "trp": 3_000_000, "dt": "2026-09-01T10:00:00Z"},
        {"pi": "7", "pn": "Flop", "tty": 2, "trp": 500_000, "dt": "2026-09-01T18:00:00Z"},
    ]
    review = analyze_own_transfers(transfers)
    assert review.buys == 1 and review.sells == 1
    assert review.realized_pnl == -2_500_000
    assert len(review.quick_flips) == 1


def test_rival_needs_detect_position_gaps():
    squads = {
        "m1": [_player(position="Torwart"), _player(position="Abwehr")],
        "m2": [_player(position="Abwehr") for _ in range(3)]
        + [_player(position="Mittelfeld") for _ in range(3)]
        + [_player(position="Sturm"), _player(position="Torwart")],
    }
    needs = analyze_rival_needs(squads, {"m1": "Dünn", "m2": "Voll"})
    thin = next(n for n in needs if n.manager == "Dünn")
    full = next(n for n in needs if n.manager == "Voll")
    assert "Mittelfeld" in thin.gaps and "Sturm" in thin.gaps
    assert full.gaps == []


def test_cost_basis_uses_last_own_purchase_after_season_reset():
    """Käufe aus der Vorsaison dürfen den Einstandspreis nicht verfälschen."""
    from kickbase_agent.analytics import cost_basis_from_history, season_start_from_history

    history = [
        {"t": 2, "trp": 15_661_390, "unm": "yannolmaker", "dt": "2025-08-19T16:07:47Z"},
        {"t": 4, "trp": 0, "dt": "2026-08-07T16:21:10Z"},  # Saison-Reset
        {"t": 2, "trp": 31_852_111, "unm": "yannolmaker", "dt": "2026-08-14T20:07:33Z"},
        {"t": 2, "trp": 9_000_000, "unm": "Rivale", "dt": "2026-08-20T10:00:00Z"},
    ]
    season_start = season_start_from_history(history)
    assert season_start == "2026-08-07"

    cost, bought_at = cost_basis_from_history(history, "yannolmaker", season_start)
    assert cost == 31_852_111
    assert bought_at == "2026-08-14"


def test_cost_basis_returns_none_without_own_purchase():
    from kickbase_agent.analytics import cost_basis_from_history

    history = [{"t": 2, "trp": 5_000_000, "unm": "Rivale", "dt": "2026-08-14T20:07:33Z"}]
    assert cost_basis_from_history(history, "yannolmaker", None) == (None, None)


def test_premium_uses_listing_day_market_value_not_todays():
    """Ein gestiegener Marktwert darf ein Angebot nicht als Schnäppchen erscheinen lassen.

    Unter Marktwert kaufen ist in Kickbase nicht möglich; ein Preis unter dem heutigen
    Marktwert bedeutet nur, dass der Wert seit dem Einstellen gestiegen ist.
    """
    from kickbase_agent.analytics import analyze_market_premiums

    offer = MarketOffer(
        player=_player(id="p9", market_value=6_310_485),  # heute
        price=5_345_826,
        expires_in_seconds=None,
        seller="Rivale",
    )

    naive = analyze_market_premiums([offer])
    assert naive.reference == "today"
    assert naive.median_premium < 0  # sieht fälschlich nach Rabatt aus
    assert naive.stale_listings == 1

    korrekt = analyze_market_premiums([offer], {"p9": 5_100_000})
    assert korrekt.reference == "listing"
    assert korrekt.median_premium > 0  # tatsächlich ein Aufschlag


def test_market_value_on_reads_history_by_date():
    from datetime import date, timedelta

    from kickbase_agent.analytics import market_value_on

    epoch = date(1970, 1, 1)
    day = date(2026, 8, 14)
    history = {
        "it": [
            {"dt": (day - epoch).days - 1, "mv": 30_000_000},
            {"dt": (day - epoch).days, "mv": 30_955_729},
            {"dt": (day - epoch).days + 1, "mv": 31_500_000},
        ]
    }
    assert market_value_on(history, "2026-08-14") == 30_955_729
    assert market_value_on(history, day) == 30_955_729
    # Fehlender Tag wird aus der Nachbarschaft aufgefüllt …
    assert market_value_on(history, date(2026, 8, 16)) == 31_500_000
    # … aber nicht über die Toleranz hinaus.
    assert market_value_on(history, date(2026, 12, 1)) is None
    assert market_value_on(None, "2026-08-14") is None


def test_purchase_review_separates_overpay_from_later_drift():
    from kickbase_agent.analytics import PurchaseReview

    posch = PurchaseReview(
        player="Posch",
        bought_at="2026-08-18",
        price=13_252_111,
        market_value_then=9_957_448,
        market_value_now=8_885_142,
    )
    assert round(posch.overpay_pct * 100, 1) == 33.1
    assert posch.overpay == 13_252_111 - 9_957_448
    assert posch.value_change_since == 8_885_142 - 9_957_448

    unbekannt = PurchaseReview("X", "2026-08-18", 1_000, None, None)
    assert unbekannt.overpay is None and unbekannt.overpay_pct is None
