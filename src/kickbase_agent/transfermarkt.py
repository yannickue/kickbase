"""Transfermarkt als Ausfallquelle mit voraussichtlichen Rückkehrdaten.

Ligainsider sagt, *seit wann* jemand fehlt; Transfermarkt sagt zusätzlich, *bis wann*
(Spalte "bis ca."). Für Kickbase ist genau das die entscheidende Information: Ein
Spieler, der in zehn Tagen zurückkommt, ist ein Kaufkandidat am Wertetief — einer mit
Rückkehr in drei Monaten ist totes Kapital.

`robots.txt` von transfermarkt.de erlaubt zum Implementierungszeitpunkt allgemeines
Crawling ("User-agent: *  Allow: /"; lediglich `wget` ist ausgeschlossen).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime

import requests

BASE_URL = "https://www.transfermarkt.de"
INJURY_PATH = "/bundesliga/verletztespieler/wettbewerb/L1"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
TIMEOUT = 25


@dataclass
class Absence:
    player: str
    position: str | None
    club: str | None
    reason: str | None
    return_date: date | None
    market_value: str | None

    def days_until_return(self, today: date | None = None) -> int | None:
        if self.return_date is None:
            return None
        return (self.return_date - (today or date.today())).days


@dataclass
class TransfermarktData:
    absences: list[Absence] = field(default_factory=list)
    available: bool = True
    error: str | None = None


def _parse_date(text: str) -> date | None:
    match = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", text or "")
    if not match:
        return None
    try:
        return datetime.strptime(match.group(), "%d.%m.%Y").date()
    except ValueError:
        return None


def _split_name_and_position(text: str) -> tuple[str, str | None]:
    """Transfermarkt schreibt Name und Position in dieselbe Zelle, doppelt und ohne Trenner.

    Beispiel: "Tiago Tomás Rechtsaußen" -> ("Tiago Tomás", "Rechtsaußen").
    Die Position ist immer der letzte Begriff aus einer bekannten Positionsliste.
    """
    positions = (
        "Torwart", "Innenverteidiger", "Linker Verteidiger", "Rechter Verteidiger",
        "Abwehr", "Defensives Mittelfeld", "Zentrales Mittelfeld",
        "Offensives Mittelfeld", "Linkes Mittelfeld", "Rechtes Mittelfeld",
        "Mittelfeld", "Linksaußen", "Rechtsaußen", "Hängende Spitze",
        "Mittelstürmer", "Sturm",
    )
    cleaned = " ".join((text or "").split())
    for pos in sorted(positions, key=len, reverse=True):
        if cleaned.endswith(pos):
            return cleaned[: -len(pos)].strip(), pos
    return cleaned, None


class TransfermarktClient:
    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def _get(self, path: str) -> str:
        resp = self.session.get(f"{BASE_URL}{path}", timeout=TIMEOUT)
        resp.raise_for_status()
        time.sleep(1.0)  # Transfermarkt bewusst schonend abfragen
        return resp.text

    def fetch_absences(self) -> list[Absence]:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(self._get(INJURY_PATH), "lxml")
        table = soup.select_one("table.items")
        if table is None:
            return []

        out: list[Absence] = []
        for row in table.select("tbody > tr"):
            cells = row.find_all("td", recursive=False)
            if len(cells) != 5:
                continue
            name, position = _split_name_and_position(cells[0].get_text(" ", strip=True))
            if not name:
                continue
            club_link = cells[1].find("a")
            club = None
            if club_link is not None:
                club = club_link.get("title") or club_link.get_text(strip=True) or None
            out.append(
                Absence(
                    player=name,
                    position=position,
                    club=club,
                    reason=" ".join(cells[2].get_text(" ", strip=True).split()) or None,
                    return_date=_parse_date(cells[3].get_text(" ", strip=True)),
                    market_value=" ".join(cells[4].get_text(" ", strip=True).split()) or None,
                )
            )
        return out


def collect(client: TransfermarktClient | None = None) -> TransfermarktData:
    """Ausfälle sammeln; ein Fehler macht die Analyse ärmer, nicht kaputt."""
    client = client or TransfermarktClient()
    data = TransfermarktData()
    try:
        data.absences = client.fetch_absences()
    except Exception as exc:
        data.available = False
        data.error = f"Transfermarkt nicht abrufbar: {exc}"
    return data
