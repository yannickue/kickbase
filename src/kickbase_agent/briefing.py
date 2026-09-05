"""Zusammenbau des Tiefen-Briefings aus Kickbase-Daten, Ligainsider und Analytics.

Das Ergebnis ist bewusst ein dichtes, aber lesbares Textdokument: Es dient entweder als
Prompt für die Claude-API oder — im `--no-claude`-Modus — als Vorlage, die direkt in einem
Claude-Gespräch ausgewertet wird.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from . import ligainsider as li
from .analytics import (
    MarketPremiumStats,
    PlayerSignals,
    RivalNeed,
    TransferReview,
    analyze_market_premiums,
    analyze_own_transfers,
    analyze_rival_needs,
    compute_player_signals,
    cost_basis_from_history,
    season_start_from_history,
)
from .kickbase_client import KickbaseClient, MarketOffer, Player, _pick

# Wie viele Marktkandidaten maximal tief analysiert werden (jeder kostet 3 API-Calls).
MAX_MARKET_DEEP_DIVE = 18
# Kurze Pause zwischen Detail-Calls, um die API nicht zu hämmern.
API_DELAY = 0.25


@dataclass
class DeepBriefing:
    league_name: str
    manager_name: str | None = None
    budget: float | None = None
    team_value: float | None = None
    placement: int | None = None
    total_points: float | None = None
    transfer_profit: float | None = None
    matchday: int | None = None

    own_squad: list[tuple[Player, PlayerSignals]] = field(default_factory=list)
    # Spieler-ID -> (Einstandspreis, Kaufdatum) für die laufende Saison
    cost_basis: dict[str, tuple[float | None, str | None]] = field(default_factory=dict)
    market: list[tuple[MarketOffer, PlayerSignals | None]] = field(default_factory=list)
    premium_stats: MarketPremiumStats | None = None
    transfer_review: TransferReview | None = None
    rival_needs: list[RivalNeed] = field(default_factory=list)
    table: list[dict[str, Any]] = field(default_factory=list)
    ligainsider: li.LigainsiderData | None = None
    notes: list[str] = field(default_factory=list)


def _match_clubs_for_news(kickbase_team_names: set[str]) -> list[str]:
    """Ordnet Kickbase-Kurznamen ("Dortmund") den Ligainsider-Vereinen zu."""
    matched: list[str] = []
    for li_club in li.TEAM_PAGES:
        normalized = li_club.lower()
        for kb_name in kickbase_team_names:
            token = (kb_name or "").lower().replace("m'gladbach", "mönchengladbach")
            if token and (token in normalized or normalized.endswith(token)):
                matched.append(li_club)
                break
    return matched


def build_deep_briefing(
    client: KickbaseClient,
    league_id: str,
    *,
    with_ligainsider: bool = True,
    max_market: int = MAX_MARKET_DEEP_DIVE,
    progress: Any = None,
) -> DeepBriefing:
    """Holt alle relevanten Daten und verdichtet sie zu einem Briefing."""

    def log(msg: str) -> None:
        if progress:
            progress(msg)

    me = client.get_me(league_id)
    league_name = me["league_name"] or f"Liga {league_id}"
    brief = DeepBriefing(league_name=league_name, budget=me["budget"])

    manager_id = client.user_id or ""
    if manager_id:
        dash = client.get_manager_dashboard(league_id, manager_id)
        brief.manager_name = _pick(dash, "unm", default=None)
        brief.team_value = _pick(dash, "tv", default=None)
        brief.placement = _pick(dash, "pl", default=None)
        brief.total_points = _pick(dash, "tp", default=None)
        brief.transfer_profit = _pick(dash, "prft", default=None)

    log("Kader wird geladen…")
    squad = client.get_squad(league_id)
    market, day = client.get_market(league_id)
    brief.matchday = day

    team_names = client.get_team_names(me["raw"].get("cpi", "1"))
    for p in squad + [o.player for o in market]:
        if p.team_id and p.team_id in team_names:
            p.team = team_names[p.team_id]

    if day is not None:
        brief.table = client.get_ranking(league_id, day)

    # --- Tiefenanalyse eigener Kader ------------------------------------------
    log(f"Tiefendaten für {len(squad)} eigene Spieler…")
    for player in squad:
        sig = _deep_player(client, league_id, player.id)
        if sig:
            sig.position = player.position
            sig.team = player.team or sig.team
            brief.own_squad.append((player, sig))
        else:
            brief.own_squad.append((player, PlayerSignals(player_id=player.id, name=player.name)))

    # --- Einstandspreise: was wurde für den aktuellen Kader tatsächlich bezahlt --
    if brief.manager_name:
        log("Einstandspreise des Kaders…")
        season_start: str | None = None
        histories: dict[str, list[dict[str, Any]]] = {}
        for player in squad:
            try:
                histories[player.id] = client.get_player_transfer_history(league_id, player.id)
            except Exception:
                continue
            time.sleep(API_DELAY)
        for hist in histories.values():
            found = season_start_from_history(hist)
            if found and (season_start is None or found > season_start):
                season_start = found
        for player_id, hist in histories.items():
            brief.cost_basis[player_id] = cost_basis_from_history(
                hist, brief.manager_name, season_start
            )

    # --- Tiefenanalyse relevanter Marktkandidaten ------------------------------
    budget = brief.budget or 0
    def candidate_rank(offer: MarketOffer) -> tuple[int, float]:
        affordable = 0 if (offer.price or 0) <= budget else 1
        return (affordable, -(offer.player.market_value or 0))

    ranked = sorted(market, key=candidate_rank)
    deep_targets = ranked[:max_market]
    log(f"Tiefendaten für {len(deep_targets)} von {len(market)} Marktspielern…")
    deep_ids = {o.player.id for o in deep_targets}
    for offer in market:
        if offer.player.id in deep_ids:
            sig = _deep_player(client, league_id, offer.player.id)
            if sig:
                sig.position = offer.player.position
                sig.team = offer.player.team or sig.team
            brief.market.append((offer, sig))
        else:
            brief.market.append((offer, None))

    brief.premium_stats = analyze_market_premiums(market)

    # --- Eigene Transferhistorie ------------------------------------------------
    if manager_id:
        log("Eigene Transferhistorie…")
        brief.transfer_review = analyze_own_transfers(
            client.get_manager_transfers(league_id, manager_id)
        )

    # --- Konkurrenz --------------------------------------------------------------
    log("Konkurrenzkader…")
    managers = client.get_managers(league_id)
    names = {m["id"]: m["name"] for m in managers}
    squads: dict[str, list[Player]] = {}
    for m in managers:
        if m["id"] == manager_id:
            continue
        try:
            squads[m["id"]] = client.get_manager_squad(league_id, m["id"])
        except Exception as exc:  # einzelner Manager darf fehlschlagen
            brief.notes.append(f"Kader von {m['name']} nicht abrufbar: {exc}")
        time.sleep(API_DELAY)
    for rival_squad in squads.values():
        for p in rival_squad:
            if p.team_id and p.team_id in team_names:
                p.team = team_names[p.team_id]
    brief.rival_needs = analyze_rival_needs(squads, names)

    # --- Ligainsider ---------------------------------------------------------------
    if with_ligainsider:
        log("Ligainsider (Verletzungen, News, Aufstellungen)…")
        relevant_teams = {p.team for p, _ in brief.own_squad if p.team}
        relevant_teams |= {o.player.team for o, s in brief.market if s and o.player.team}
        clubs = _match_clubs_for_news(relevant_teams)
        brief.ligainsider = li.collect(clubs=clubs or None)
        if brief.ligainsider and not brief.ligainsider.available:
            brief.notes.append(brief.ligainsider.error or "Ligainsider nicht verfügbar")

    return brief


def _deep_player(client: KickbaseClient, league_id: str, player_id: str) -> PlayerSignals | None:
    """Detail + Marktwertverlauf + Leistungshistorie eines Spielers einsammeln."""
    if not player_id:
        return None
    try:
        detail = client.get_player_detail(league_id, player_id)
    except Exception:
        return None
    mv_hist = None
    perf = None
    try:
        mv_hist = client.get_player_market_value_history(league_id, player_id, 92)
    except Exception:
        pass
    try:
        perf = client.get_player_performance(league_id, player_id)
    except Exception:
        pass
    time.sleep(API_DELAY)
    return compute_player_signals(detail, mv_hist, perf)


# --------------------------------------------------------------------------------------
# Formatierung
# --------------------------------------------------------------------------------------


def _eur(value: float | None) -> str:
    if value is None:
        return "?"
    return f"{value:,.0f}€".replace(",", ".")


def _signed_eur(value: float | None) -> str:
    if value is None:
        return "?"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:,.0f}€".replace(",", ".")


def _num(value: float | None, digits: int = 1) -> str:
    return "?" if value is None else f"{value:.{digits}f}"


def _pct(value: float | None) -> str:
    return "?" if value is None else f"{value * 100:.0f}%"


def _signal_line(sig: PlayerSignals | None) -> str:
    if sig is None:
        return "keine Tiefendaten geladen"
    bits = [
        f"Einsätze {sig.matches_played}/{sig.matchdays}",
        f"Min/Spieltag {_num(sig.minutes_per_matchday, 0)}",
        f"Pkt/90 {_num(sig.points_per_90, 0)}",
        f"Prior-Pkt/90 {_num(sig.prior_points_per_90, 0)} ({sig.prior_matches} Sp.)",
        f"Erwartung/Spiel {_num(sig.expected_points_next_match, 0)}",
    ]
    if sig.mv_change_24h is not None:
        bits.append(f"MW 24h {_signed_eur(sig.mv_change_24h)}")
    if sig.mv_change_7d is not None:
        bits.append(f"7T {_signed_eur(sig.mv_change_7d)}")
    if sig.mv_change_30d is not None:
        bits.append(f"30T {_signed_eur(sig.mv_change_30d)}")
    if sig.mv_range_position is not None:
        bits.append(f"Lage in 92T-Spanne {_pct(sig.mv_range_position)}")
    if sig.mv_history_days is not None and sig.mv_history_days < 30:
        bits.append(f"⚠ erst {sig.mv_history_days} Tage in Kickbase gelistet")
    return " | ".join(bits)


def format_briefing(brief: DeepBriefing) -> str:
    """Erzeugt das Textbriefing."""
    injuries = brief.ligainsider.by_player_key() if brief.ligainsider else {}

    def injury_note(name: str) -> str:
        entry = injuries.get(li.normalize_name(name))
        if not entry:
            return ""
        parts = [entry.status or "Ausfall"]
        if entry.reason:
            parts.append(entry.reason)
        if entry.since:
            parts.append(f"seit {entry.since}")
        if entry.news:
            parts.append(f"News: {entry.news}")
        return "  ⚠ LIGAINSIDER: " + " | ".join(parts)

    out: list[str] = []
    out.append(f"# Kickbase-Tiefenanalyse — {brief.league_name}")
    out.append("")
    out.append("## Ausgangslage")
    out.append(f"- Manager: {brief.manager_name or '?'}")
    out.append(f"- Spieltag: {brief.matchday if brief.matchday is not None else '?'}")
    out.append(f"- Budget: {_eur(brief.budget)}")
    out.append(f"- Teamwert: {_eur(brief.team_value)}")
    out.append(f"- Tabellenplatz: {brief.placement if brief.placement is not None else '?'}")
    out.append(f"- Saisonpunkte: {brief.total_points if brief.total_points is not None else '?'}")
    out.append(f"- Bisheriger Transfergewinn: {_signed_eur(brief.transfer_profit)}")
    out.append("")

    # --- eigener Kader -------------------------------------------------------
    out.append("## Eigener Kader (mit abgeleiteten Prädiktoren)")
    out.append(
        "_Pkt/90 = Punkte je 90 Minuten. Prior = Vorsaison. Erwartung/Spiel = "
        "Shrinkage-Schätzer aus aktueller Saison + Vorsaison, skaliert auf die "
        "erwartete Einsatzzeit._"
    )
    total_cost = 0.0
    total_value = 0.0
    for player, sig in brief.own_squad:
        head = f"- **{player.name}** ({player.position or '?'}, {player.team or '?'}) — MW {_eur(player.market_value)}"
        cost, bought_at = brief.cost_basis.get(player.id, (None, None))
        if cost:
            pnl = (player.market_value or 0) - cost
            total_cost += cost
            total_value += player.market_value or 0
            head += (
                f" | Einstand {_eur(cost)} am {bought_at} → **{_signed_eur(pnl)}** "
                f"({pnl / cost * 100:+.1f}%)"
            )
        if player.status and player.status != "fit":
            head += f" — Kickbase-Status: {player.status}"
        out.append(head)
        out.append(f"  {_signal_line(sig)}")
        if sig and sig.sample_warning:
            out.append(f"  ⓘ {sig.sample_warning}")
        note = injury_note(player.name)
        if note:
            out.append(note)
    if total_cost:
        delta = total_value - total_cost
        out.append("")
        out.append(
            f"**Kader-Bilanz:** {_eur(total_cost)} bezahlt, aktuell {_eur(total_value)} wert "
            f"= **{_signed_eur(delta)}** ({delta / total_cost * 100:+.1f}%) unrealisiert."
        )
    out.append("")

    # --- Markt ----------------------------------------------------------------
    out.append("## Transfermarkt")
    ps = brief.premium_stats
    if ps and ps.count:
        out.append(
            f"_Aufschlags-Niveau der Liga (nur Manager-Angebote, n={ps.count}): "
            f"Median {_pct(ps.median_premium)}, Mittel {_pct(ps.mean_premium)}, "
            f"Spanne {_pct(ps.min_premium)} bis {_pct(ps.max_premium)}._"
        )
        by_mgr = ", ".join(
            f"{m}: {_pct(sum(v) / len(v))}" for m, v in sorted(ps.by_manager.items())
        )
        out.append(f"_Durchschnittlicher Aufschlag je Anbieter: {by_mgr}_")
    out.append("")
    for offer, sig in brief.market:
        p = offer.player
        premium = ""
        if offer.price and p.market_value:
            premium = f" ({(offer.price / p.market_value - 1) * 100:+.1f}% zum MW)"
        head = (
            f"- **{p.name}** ({p.position or '?'}, {p.team or '?'}) — "
            f"Preis {_eur(offer.price)}{premium}, MW {_eur(p.market_value)}, "
            f"Anbieter: {offer.seller or 'Kickbase'}"
        )
        if offer.expires_in_seconds is not None:
            head += f", läuft in {offer.expires_in_seconds / 3600:.1f} Std. ab"
        if p.status and p.status != "fit":
            head += f" — Kickbase-Status: {p.status}"
        out.append(head)
        if sig:
            out.append(f"  {_signal_line(sig)}")
            if sig.sample_warning:
                out.append(f"  ⓘ {sig.sample_warning}")
        note = injury_note(p.name)
        if note:
            out.append(note)
    out.append("")

    # --- eigene Transferhistorie -------------------------------------------------
    tr = brief.transfer_review
    if tr:
        out.append("## Eigene Transferhistorie (Verhaltensmuster)")
        out.append(
            f"- Käufe: {tr.buys} ({_eur(tr.spent)}) | Verkäufe: {tr.sells} ({_eur(tr.earned)})"
        )
        out.append(f"- Realisiertes Ergebnis abgeschlossener Geschäfte: {_signed_eur(tr.realized_pnl)}")
        if tr.round_trips:
            out.append("- Abgeschlossene Kauf-/Verkauf-Paare:")
            for rt in tr.round_trips[-12:]:
                held = f"{rt['held_days']:.1f} Tage" if rt["held_days"] is not None else "?"
                out.append(
                    f"  - {rt['player']}: Kauf {_eur(rt['buy_price'])} → Verkauf "
                    f"{_eur(rt['sell_price'])} = {_signed_eur(rt['pnl'])} (gehalten {held})"
                )
        if tr.quick_flips:
            out.append(
                f"- ⚠ {len(tr.quick_flips)} Geschäft(e) mit Haltedauer unter 7 Tagen — "
                "Marktwertänderungen brauchen typischerweise länger, um einen Aufschlag zu verdienen."
            )
        out.append("")

    # --- Konkurrenz ------------------------------------------------------------
    if brief.rival_needs:
        out.append("## Konkurrenz (wer hätte Bedarf / wer könnte kaufen)")
        for r in brief.rival_needs:
            gaps = ", ".join(r.gaps) if r.gaps else "keine offensichtliche Lücke"
            positions = ", ".join(f"{k}: {v}" for k, v in sorted(r.by_position.items()))
            out.append(
                f"- **{r.manager}** — Kadergröße {r.squad_size}, Teamwert {_eur(r.team_value)} | "
                f"{positions} | Lücken: {gaps}"
            )
        out.append("")

    # --- Tabelle ---------------------------------------------------------------
    if brief.table:
        out.append("## Tabelle")
        for row in sorted(brief.table, key=lambda r: r.get("season_rank") or 999):
            out.append(
                f"- Platz {row.get('season_rank', '?')}: {row.get('name', '?')} "
                f"({row.get('season_points', '?')} Punkte)"
            )
        out.append("")

    # --- Ligainsider-Kontext ------------------------------------------------------
    if brief.ligainsider and brief.ligainsider.available:
        out.append("## Ligainsider — Real-Life-Kontext")
        out.append(f"_{len(brief.ligainsider.injuries)} Ausfälle ligaweit erfasst._")
        for club, news in sorted(brief.ligainsider.team_news.items()):
            if not news.headlines and not news.lineup:
                continue
            out.append(f"### {club}")
            if news.lineup:
                out.append(f"- Zuletzt gemeldete Aufstellung: {news.lineup}")
            for h in news.headlines[:6]:
                out.append(f"- News: {h}")
        out.append("")

    if brief.notes:
        out.append("## Hinweise zur Datenlage")
        for n in brief.notes:
            out.append(f"- {n}")
        out.append("")

    return "\n".join(out)
