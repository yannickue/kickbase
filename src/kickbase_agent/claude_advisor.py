"""Baut aus den Kickbase-Daten einen Prompt und lässt Claude eine Empfehlung geben."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from anthropic import Anthropic

from .kickbase_client import MarketOffer, Player

DEFAULT_MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = """\
Du bist ein erfahrener Kickbase-Manager-Berater (Fußball-Fantasy-Manager, Bundesliga).
Du bekommst den aktuellen Kontostand, Kader, Transfermarkt und die Tabellensituation eines
Nutzers als strukturierte Daten. Deine Aufgabe: eine knappe, konkrete Handlungsempfehlung auf
Deutsch geben.

Beachte:
- Sei konkret: nenne Spielernamen, Preise, Beträge — keine generischen Floskeln.
- Priorisiere: was sollte der Nutzer JETZT als Erstes tun?
- Berücksichtige Budget-Limits: schlage nur Käufe vor, die sich mit dem verfügbaren Budget
  ausgehen.
- Weise auf Risiken hin (verletzte/gesperrte Spieler im eigenen Kader, auslaufende
  Marktangebote).
- Wenn Daten fehlen oder unklar sind, sag das offen statt zu raten.
- Du triffst keine automatischen Käufe/Verkäufe — du gibst nur eine Empfehlung, die der
  Nutzer selbst in der App umsetzt.

Antworte als Markdown mit genau diesen Abschnitten:
## Zusammenfassung
(2-3 Sätze: wichtigste Handlungsempfehlung zuerst)

## Transfermarkt
(konkrete Kauf-/Verkaufsvorschläge mit Begründung und Preis)

## Aufstellung & Kader
(Formation/Startelf-Hinweise, Bankspieler, Kaderlücken)

## Risiken
(Verletzungen, Sperren, auslaufende Angebote, Budgetknappheit)
"""


@dataclass
class KickbaseSnapshot:
    league_name: str
    budget: float | None
    team_value: float | None
    placement: int | None
    squad: list[Player]
    market: list[MarketOffer]
    table: list[dict[str, Any]]


def _format_player(p: Player) -> str:
    parts = [p.name]
    if p.position:
        parts.append(f"Pos: {p.position}")
    if p.market_value is not None:
        parts.append(f"MW: {p.market_value:,.0f}€".replace(",", "."))
    if p.points is not None:
        parts.append(f"Punkte: {p.points}")
    if p.status:
        parts.append(f"Status: {p.status}")
    return " | ".join(parts)


def _format_market_offer(o: MarketOffer) -> str:
    parts = [_format_player(o.player)]
    if o.price is not None:
        parts.append(f"Preis: {o.price:,.0f}€".replace(",", "."))
    if o.expires_at:
        parts.append(f"Läuft ab: {o.expires_at}")
    if o.seller:
        parts.append(f"Verkäufer: {o.seller}")
    return " | ".join(parts)


def build_user_prompt(snapshot: KickbaseSnapshot) -> str:
    lines = [
        f"# Liga: {snapshot.league_name}",
        "",
        "## Kontostand",
        f"- Budget: {snapshot.budget:,.0f}€".replace(",", ".") if snapshot.budget is not None
        else "- Budget: unbekannt",
        f"- Teamwert: {snapshot.team_value:,.0f}€".replace(",", ".") if snapshot.team_value is not None
        else "- Teamwert: unbekannt",
        f"- Tabellenplatz: {snapshot.placement}" if snapshot.placement is not None
        else "- Tabellenplatz: unbekannt",
        "",
        "## Eigener Kader",
    ]
    if snapshot.squad:
        lines += [f"- {_format_player(p)}" for p in snapshot.squad]
    else:
        lines.append("(keine Kaderdaten verfügbar)")

    lines += ["", "## Transfermarkt (verfügbare Spieler)"]
    if snapshot.market:
        lines += [f"- {_format_market_offer(o)}" for o in snapshot.market]
    else:
        lines.append("(keine Marktdaten verfügbar)")

    lines += ["", "## Tabelle"]
    if snapshot.table:
        for row in snapshot.table:
            lines.append(
                f"- Platz {row.get('rank', '?')}: {row.get('team_name', '?')} "
                f"({row.get('points', '?')} Punkte)"
            )
    else:
        lines.append("(keine Tabellendaten verfügbar)")

    return "\n".join(lines)


def get_recommendation(
    snapshot: KickbaseSnapshot,
    api_key: str,
    model: str = DEFAULT_MODEL,
) -> str:
    client = Anthropic(api_key=api_key)
    user_prompt = build_user_prompt(snapshot)
    response = client.messages.create(
        model=model,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return "".join(block.text for block in response.content if block.type == "text")
