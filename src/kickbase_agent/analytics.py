"""Ableitung von Prädiktoren aus den Kickbase-Rohdaten.

Leitgedanke: Rohe Saisonpunkte sind nach zwei Spieltagen fast wertlos — ein einzelnes Tor
sagt kaum etwas über das nächste Spiel. Belastbarer sind

1. **Rolle/Einsatzzeit** (spielt er überhaupt, und wie lange): stabil, schnell aussagekräftig,
2. **Punkte pro 90 Minuten** statt Gesamtpunkte: macht Ergänzungsspieler und Dauerbrenner
   vergleichbar,
3. **Vorsaison-Daten als Prior**: bei kleiner Stichprobe wird der aktuelle Wert Richtung
   Vorsaisonleistung geschrumpft (Shrinkage), statt Ausreißer für bare Münze zu nehmen,
4. **Marktwert-Dynamik** (Momentum, Lage in der 92-Tage-Spanne): Grundlage für Kauf-/
   Verkaufszeitpunkt und für die Frage, wie viel Aufschlag vertretbar ist.

Alle Funktionen sind rein und ohne Netzwerkzugriff, damit sie testbar bleiben.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Any

from .kickbase_client import MarketOffer, Player, _pick

SECONDS_PER_MATCH = 5400  # 90 Minuten
# Gewicht des Vorsaison-Priors, ausgedrückt in "Spielen". Bei 5 gespielten Spielen zählen
# aktuelle Saison und Prior je zur Hälfte.
SHRINKAGE_PRIOR_MATCHES = 5.0
# Ab wie vielen Spielen die aktuelle Saison als halbwegs belastbar gilt.
MIN_MATCHES_FOR_CONFIDENCE = 5


@dataclass
class PlayerSignals:
    """Abgeleitete Kennzahlen zu einem Spieler."""

    player_id: str
    name: str

    # Einsatz / Rolle
    matchdays: int = 0  # Spieltage der laufenden Saison
    matches_played: int = 0
    minutes_total: float | None = None
    minutes_per_matchday: float | None = None
    start_rate: float | None = None  # Anteil Spieltage mit Einsatz

    # Ertrag
    points_total: float | None = None
    points_per_match: float | None = None
    points_per_90: float | None = None

    # Vorsaison als Prior
    prior_matches: int = 0
    prior_points_per_90: float | None = None
    prior_minutes_per_match: float | None = None

    # Geschrumpfte Erwartung (Prognosegrundlage)
    expected_points_per_90: float | None = None
    expected_points_next_match: float | None = None

    # Marktwert
    market_value: float | None = None
    mv_change_24h: float | None = None
    mv_change_7d: float | None = None
    mv_change_30d: float | None = None
    mv_low_92d: float | None = None
    mv_high_92d: float | None = None
    mv_range_position: float | None = None  # 0 = am Tief, 1 = am Hoch
    mv_history_days: int | None = None  # Länge der belastbaren Marktwertreihe

    # Kontext
    status_code: int | None = None
    position: str | None = None
    team: str | None = None
    sample_warning: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _parse_minutes(value: Any) -> float | None:
    """Wandelt Kickbase-Minutenangaben wie "90'" oder "45'" in Zahlen."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"\d+", str(value))
    return float(match.group()) if match else None


def _current_season_block(performance: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    if not performance:
        return None
    for season in performance:
        for entry in season.get("ph", []) or []:
            if entry.get("cur"):
                return season
    return performance[-1] if performance else None


def _prior_season_block(performance: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """Die letzte abgeschlossene Saison (die vor der aktuellen)."""
    if not performance or len(performance) < 2:
        return None
    current = _current_season_block(performance)
    prior = None
    for season in performance:
        if season is current:
            break
        prior = season
    return prior


def _season_totals(season: dict[str, Any] | None) -> tuple[int, float, float]:
    """(Spiele mit Einsatz, Minuten gesamt, Punkte gesamt) einer Saison."""
    if not season:
        return 0, 0.0, 0.0
    matches = 0
    minutes = 0.0
    points = 0.0
    for entry in season.get("ph", []) or []:
        mins = _parse_minutes(entry.get("mp"))
        pts = entry.get("p")
        if mins:
            matches += 1
            minutes += mins
        if pts is not None and mins:
            points += float(pts)
    return matches, minutes, points


def compute_player_signals(
    detail: dict[str, Any],
    mv_history: dict[str, Any] | None = None,
    performance: list[dict[str, Any]] | None = None,
) -> PlayerSignals:
    """Rechnet Rohdaten eines Spielers in Prädiktoren um."""
    first = _pick(detail, "fn", default="") or ""
    last = _pick(detail, "ln", "n", default="") or ""
    sig = PlayerSignals(
        player_id=str(_pick(detail, "i", default="")),
        name=f"{first} {last}".strip() or "Unbekannt",
        market_value=_pick(detail, "mv", default=None),
        mv_change_24h=_pick(detail, "tfhmvt", default=None),
        status_code=_pick(detail, "st", default=None),
        team=_pick(detail, "tn", default=None),
    )

    # --- Einsatz in der laufenden Saison -------------------------------------
    ph = _pick(detail, "ph", default=[]) or []
    sig.matchdays = len(ph)
    sig.matches_played = sum(1 for e in ph if e.get("hp"))
    seconds = _pick(detail, "sec", default=None)
    if seconds:
        sig.minutes_total = float(seconds) / 60.0
        if sig.matchdays:
            sig.minutes_per_matchday = sig.minutes_total / sig.matchdays
    if sig.matchdays:
        sig.start_rate = sig.matches_played / sig.matchdays

    sig.points_total = _pick(detail, "tp", default=None)
    if sig.points_total is not None and sig.matches_played:
        sig.points_per_match = float(sig.points_total) / sig.matches_played
    if sig.points_total is not None and sig.minutes_total:
        sig.points_per_90 = float(sig.points_total) / (sig.minutes_total / 90.0)

    # --- Vorsaison als Prior --------------------------------------------------
    prior = _prior_season_block(performance)
    p_matches, p_minutes, p_points = _season_totals(prior)
    sig.prior_matches = p_matches
    if p_minutes:
        sig.prior_points_per_90 = p_points / (p_minutes / 90.0)
        sig.prior_minutes_per_match = p_minutes / p_matches if p_matches else None

    # --- Shrinkage: aktuelle Saison Richtung Prior ziehen ---------------------
    n = float(sig.matches_played)
    if sig.points_per_90 is not None and sig.prior_points_per_90 is not None:
        weight = n / (n + SHRINKAGE_PRIOR_MATCHES)
        sig.expected_points_per_90 = (
            weight * sig.points_per_90 + (1 - weight) * sig.prior_points_per_90
        )
    else:
        sig.expected_points_per_90 = (
            sig.points_per_90 if sig.points_per_90 is not None else sig.prior_points_per_90
        )

    # Erwartete Punkte im nächsten Spiel = Erwartung pro 90 * erwartete Einsatzzeit.
    expected_minutes = sig.minutes_per_matchday
    if not expected_minutes and sig.prior_minutes_per_match and sig.start_rate is not None:
        expected_minutes = sig.prior_minutes_per_match * sig.start_rate
    if sig.expected_points_per_90 is not None and expected_minutes:
        sig.expected_points_next_match = sig.expected_points_per_90 * (expected_minutes / 90.0)

    if sig.matches_played < MIN_MATCHES_FOR_CONFIDENCE:
        sig.sample_warning = (
            f"nur {sig.matches_played} Einsatz/Einsätze in dieser Saison — "
            f"Saisonpunkte sind hier statistisches Rauschen"
        )

    # --- Marktwertdynamik ------------------------------------------------------
    if mv_history:
        # Werte von 0 bedeuten "Spieler war zu dem Zeitpunkt noch nicht in Kickbase
        # gelistet" — nicht "Marktwert null". Als Vergleichsbasis wären sie irreführend
        # (eine Differenz in Höhe des vollen Marktwerts), deshalb gelten sie als fehlend.
        values = [
            float(item["mv"])
            for item in (mv_history.get("it") or [])
            if item.get("mv")
        ]
        if values:
            sig.mv_low_92d = mv_history.get("lmv") or min(values)
            sig.mv_high_92d = mv_history.get("hmv") or max(values)
            latest = values[-1]

            def delta(days_back: int) -> float | None:
                """Veränderung gegenüber vor `days_back` Tagen, sofern belastbar."""
                if len(values) <= days_back:
                    return None
                return latest - values[-(days_back + 1)]

            sig.mv_change_24h = sig.mv_change_24h if sig.mv_change_24h is not None else delta(1)
            sig.mv_change_7d = delta(7)
            sig.mv_change_30d = delta(30)

            span = (sig.mv_high_92d or 0) - (sig.mv_low_92d or 0)
            if span > 0:
                sig.mv_range_position = (latest - sig.mv_low_92d) / span
            # Wie viele Tage die Reihe überhaupt abdeckt — kurze Reihen bedeuten
            # "neu in Kickbase", nicht "stabiler Wert".
            sig.mv_history_days = len(values)

    sig.position = _pick(detail, "pos", default=None)
    return sig


# --------------------------------------------------------------------------------------
# Marktpreis-/Overpay-Analyse
# --------------------------------------------------------------------------------------


@dataclass
class MarketPremiumStats:
    """Wie viel Aufschlag über Marktwert wird in dieser Liga aktuell verlangt."""

    count: int = 0
    median_premium: float | None = None  # relativ, 0.10 = +10 %
    mean_premium: float | None = None
    max_premium: float | None = None
    min_premium: float | None = None
    by_manager: dict[str, list[float]] = field(default_factory=dict)


def analyze_market_premiums(offers: list[MarketOffer]) -> MarketPremiumStats:
    """Auswertung der aktuell inserierten Manager-Angebote (Preis vs. Marktwert).

    Nur Angebote von Liga-Mitgliedern zählen — Kickbase-eigene Angebote stehen per
    Definition exakt auf Marktwert und würden die Statistik verwässern.
    """
    stats = MarketPremiumStats()
    premiums: list[float] = []
    for offer in offers:
        mv = offer.player.market_value
        if not offer.seller or not mv or not offer.price:
            continue
        premium = offer.price / mv - 1.0
        premiums.append(premium)
        stats.by_manager.setdefault(offer.seller, []).append(premium)

    if premiums:
        stats.count = len(premiums)
        stats.median_premium = statistics.median(premiums)
        stats.mean_premium = statistics.fmean(premiums)
        stats.max_premium = max(premiums)
        stats.min_premium = min(premiums)
    return stats


@dataclass
class TransferReview:
    """Auswertung der eigenen Transferhistorie."""

    buys: int = 0
    sells: int = 0
    spent: float = 0.0
    earned: float = 0.0
    realized_pnl: float = 0.0  # nur für abgeschlossene Kauf-Verkauf-Paare
    round_trips: list[dict[str, Any]] = field(default_factory=list)
    quick_flips: list[dict[str, Any]] = field(default_factory=list)  # < 7 Tage gehalten


def analyze_own_transfers(transfers: list[dict[str, Any]]) -> TransferReview:
    """Rechnet Käufe und Verkäufe zu realisierten Gewinnen/Verlusten zusammen.

    `tty` ist der Vorgangstyp: 1 = Kauf, 2 = Verkauf. Die Liste kommt neueste zuerst,
    wird hier also chronologisch verarbeitet.
    """
    from datetime import datetime

    review = TransferReview()
    ordered = sorted(transfers, key=lambda t: t.get("dt") or "")
    open_buys: dict[str, list[dict[str, Any]]] = {}

    def parse_dt(value: Any) -> Any:
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None

    for t in ordered:
        pid = str(t.get("pi") or "")
        price = float(t.get("trp") or 0)
        ttype = t.get("tty")
        if ttype == 1:  # Kauf
            review.buys += 1
            review.spent += price
            open_buys.setdefault(pid, []).append(t)
        elif ttype == 2:  # Verkauf
            review.sells += 1
            review.earned += price
            queue = open_buys.get(pid) or []
            if queue:
                buy = queue.pop(0)
                buy_price = float(buy.get("trp") or 0)
                pnl = price - buy_price
                review.realized_pnl += pnl
                bought_at = parse_dt(buy.get("dt"))
                sold_at = parse_dt(t.get("dt"))
                held_days = (
                    (sold_at - bought_at).total_seconds() / 86400
                    if bought_at and sold_at
                    else None
                )
                record = {
                    "player": t.get("pn") or buy.get("pn"),
                    "buy_price": buy_price,
                    "sell_price": price,
                    "pnl": pnl,
                    "held_days": held_days,
                    "counterparty_sell": t.get("othnm"),
                    "counterparty_buy": buy.get("othnm"),
                }
                review.round_trips.append(record)
                if held_days is not None and held_days < 7:
                    review.quick_flips.append(record)
    return review


def cost_basis_from_history(
    history: list[dict[str, Any]],
    my_manager_name: str,
    season_start: str | None = None,
) -> tuple[float | None, str | None]:
    """Ermittelt Einstandspreis und Kaufdatum eines Spielers aus seiner Transferhistorie.

    In der Historie steht bei einem Kauf (`t` = 2 mit Preis) der **Käufer** in `unm`.
    Ein Eintrag mit `t` = 4 markiert den Saison-Reset; Käufe davor gehören zur Vorsaison
    und werden über `season_start` ausgefiltert.

    Gibt (Preis, Datum) des letzten eigenen Kaufs zurück, sonst (None, None).
    """
    mine = [
        h
        for h in history
        if h.get("unm") == my_manager_name
        and h.get("trp")
        and (season_start is None or str(h.get("dt") or "") >= season_start)
    ]
    if not mine:
        return None, None
    last = max(mine, key=lambda h: str(h.get("dt") or ""))
    return float(last["trp"]), str(last.get("dt") or "")[:10]


def season_start_from_history(history: list[dict[str, Any]]) -> str | None:
    """Zeitpunkt des letzten Saison-Resets (`t` = 4) aus einer Transferhistorie."""
    resets = [str(h.get("dt")) for h in history if h.get("t") == 4 and h.get("dt")]
    return max(resets)[:10] if resets else None


# --------------------------------------------------------------------------------------
# Konkurrenzanalyse
# --------------------------------------------------------------------------------------

POSITION_MIN_REQUIRED = {"Torwart": 1, "Abwehr": 3, "Mittelfeld": 3, "Sturm": 1}


@dataclass
class RivalNeed:
    manager: str
    manager_id: str
    squad_size: int
    by_position: dict[str, int] = field(default_factory=dict)
    gaps: list[str] = field(default_factory=list)
    team_value: float | None = None


def analyze_rival_needs(
    squads: dict[str, list[Player]], manager_names: dict[str, str] | None = None
) -> list[RivalNeed]:
    """Ermittelt, auf welchen Positionen die Konkurrenz dünn besetzt ist.

    Das ist die Grundlage für die Frage "wer würde mir diesen Spieler abkaufen": Ein
    Manager mit Lücke auf einer Position ist ein wahrscheinlicherer Bieter.
    """
    names = manager_names or {}
    result: list[RivalNeed] = []
    for manager_id, squad in squads.items():
        counts: dict[str, int] = {}
        for p in squad:
            if p.position:
                counts[p.position] = counts.get(p.position, 0) + 1
        gaps = [
            pos
            for pos, required in POSITION_MIN_REQUIRED.items()
            if counts.get(pos, 0) < required
        ]
        result.append(
            RivalNeed(
                manager=names.get(manager_id, manager_id),
                manager_id=manager_id,
                squad_size=len(squad),
                by_position=counts,
                gaps=gaps,
                team_value=sum(p.market_value for p in squad if p.market_value) or None,
            )
        )
    return sorted(result, key=lambda r: r.squad_size)
