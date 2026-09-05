"""Spielplan-Daten aus der OpenLigaDB.

OpenLigaDB (https://www.openligadb.de) ist eine offene, community-betriebene API für
deutsche Fußballdaten, ausdrücklich zur freien Nutzung gedacht — kein Scraping, keine
Registrierung, keine Zugangsdaten.

Nutzen für Kickbase: Der nächste Gegner ist ein handfester Prädiktor. Ein Abwehrspieler
gegen einen Abstiegskandidaten hat eine deutlich bessere Chance auf ein zu-Null-Spiel als
derselbe Spieler gegen den Tabellenführer. Ohne Spielplan fehlt der Analyse diese Ebene
komplett.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import requests

BASE_URL = "https://api.openligadb.de"
LEAGUE = "bl1"  # 1. Bundesliga
TIMEOUT = 20


@dataclass
class Fixture:
    matchday: int | None
    kickoff: str | None
    home_team: str
    away_team: str
    finished: bool = False

    def opponent_of(self, team: str) -> tuple[str, bool] | None:
        """Gibt (Gegner, ist_heimspiel) zurück, falls `team` beteiligt ist."""
        if _same_team(team, self.home_team):
            return self.away_team, True
        if _same_team(team, self.away_team):
            return self.home_team, False
        return None


@dataclass
class FixtureData:
    matchday: int | None = None
    fixtures: list[Fixture] = field(default_factory=list)
    available: bool = True
    error: str | None = None

    def next_opponent(self, team: str) -> tuple[str, bool] | None:
        for fixture in self.fixtures:
            found = fixture.opponent_of(team)
            if found:
                return found
        return None


def _normalize(name: str) -> str:
    """Vereinsnamen grob vergleichbar machen (Kickbase kürzt, OpenLigaDB schreibt aus)."""
    lowered = (name or "").lower()
    for noise in (
        "1. fc ", "1. fsv ", "fc ", "sc ", "sv ", "tsg ", "vfb ", "vfl ", "bv ",
        "borussia ", "eintracht ", "bayer 04 ", "bayer ", "rb ", "sg ", "07 ", "1899 ",
        "hamburger ", "münchen", "muenchen", "berlin", "e.v.", " 04", " 05", " 96",
    ):
        lowered = lowered.replace(noise, " ")
    lowered = lowered.replace("ö", "o").replace("ü", "u").replace("ä", "a").replace("ß", "ss")
    return " ".join(lowered.split())


def _same_team(a: str, b: str) -> bool:
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def _get(path: str) -> Any:
    resp = requests.get(f"{BASE_URL}{path}", timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def current_matchday(season: int) -> int | None:
    try:
        data = _get(f"/getcurrentgroup/{LEAGUE}")
        return data.get("groupOrderID")
    except Exception:
        return None


def collect(season: int, matchday: int | None = None) -> FixtureData:
    """Spielpaarungen eines Spieltags. Ohne Angabe wird der aktuelle Spieltag genommen."""
    data = FixtureData()
    try:
        day = matchday or current_matchday(season)
        if day is None:
            raise ValueError("Aktueller Spieltag nicht ermittelbar")
        data.matchday = day
        raw = _get(f"/getmatchdata/{LEAGUE}/{season}/{day}")
        for match in raw or []:
            data.fixtures.append(
                Fixture(
                    matchday=(match.get("group") or {}).get("groupOrderID"),
                    kickoff=match.get("matchDateTimeUTC"),
                    home_team=((match.get("team1") or {}).get("teamName") or "?"),
                    away_team=((match.get("team2") or {}).get("teamName") or "?"),
                    finished=bool(match.get("matchIsFinished")),
                )
            )
    except Exception as exc:
        data.available = False
        data.error = f"OpenLigaDB nicht abrufbar: {exc}"
    return data
