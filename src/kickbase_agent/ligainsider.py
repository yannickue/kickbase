"""Scraper für Ligainsider (Real-Life-Informationen zur Bundesliga).

Liefert die Signale, die Kickbase selbst nicht kennt:
- Verletzungen, Sperren, Aufbautraining, "nicht im Kader" (inkl. Dauer und News-Schlagzeile)
- Vereins-News (Trainer-/PK-Aussagen)
- die zuletzt gemeldete Aufstellung pro Verein

`robots.txt` von ligainsider.de erlaubt zum Implementierungszeitpunkt das Crawlen aller
Pfade ("Disallow:" ohne Wert). Trotzdem wird bewusst langsam und mit wenigen Requests
gearbeitet (siehe `REQUEST_DELAY`), weil das hier ein privates Auswertungstool ist.

HTML-Struktur ist naturgemäß fragil: Ändert Ligainsider das Markup, liefern die Parser
leere Listen statt falscher Daten — der Rest der Analyse läuft dann ohne diese Quelle
weiter (siehe `available`-Flag im Ergebnis).
"""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any

import requests

BASE_URL = "https://www.ligainsider.de"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
REQUEST_DELAY = 0.7  # Sekunden zwischen Requests, um die Seite nicht zu belasten
TIMEOUT = 20

# Ligainsider-Pfade der Bundesliga-Vereine (Slug + interne ID), Stand Saison 26/27.
TEAM_PAGES: dict[str, str] = {
    "FC Bayern München": "/fc-bayern-muenchen/1/",
    "Borussia Dortmund": "/borussia-dortmund/14/",
    "Bayer 04 Leverkusen": "/bayer-04-leverkusen/4/",
    "RB Leipzig": "/rb-leipzig/1311/",
    "Eintracht Frankfurt": "/eintracht-frankfurt/3/",
    "VfB Stuttgart": "/vfb-stuttgart/12/",
    "SC Freiburg": "/sc-freiburg/18/",
    "TSG Hoffenheim": "/tsg-hoffenheim/10/",
    "SV Werder Bremen": "/sv-werder-bremen/2/",
    "Borussia Mönchengladbach": "/borussia-moenchengladbach/5/",
    "1. FSV Mainz 05": "/1-fsv-mainz-05/17/",
    "FC Augsburg": "/fc-augsburg/21/",
    "1. FC Union Berlin": "/1-fc-union-berlin/1246/",
    "1. FC Köln": "/1-fc-koeln/15/",
    "Hamburger SV": "/hamburger-sv/9/",
    "FC Schalke 04": "/fc-schalke-04/13/",
    "SC Paderborn 07": "/sc-paderborn-07/1249/",
    "SV 07 Elversberg": "/sv-07-elversberg/1331/",
}

# Ligainsider-Statusarten, absteigend nach Schwere des Ausfallrisikos.
STATUS_SEVERITY = {
    "Verletzung": 5,
    "Rote Karte": 5,
    "Gelb-Rote Karte": 5,
    "Gelbsperre": 5,
    "Sperre": 5,
    "Aufbautraining": 3,
    "Schwerer angeschlagen": 3,
    "Angeschlagen": 2,
    "Nicht im Kader": 4,
    "Trainingsrückstand": 3,
}


@dataclass
class InjuryEntry:
    """Ein Eintrag aus der Verletzungs-/Sperrenliste."""

    player: str
    club: str | None
    status: str | None  # z.B. "Verletzung", "Aufbautraining", "Rote Karte"
    reason: str | None  # z.B. "Kreuzbandriss"
    news: str | None  # letzte Schlagzeile zu diesem Status
    since: str | None  # z.B. "2 Monaten und 2 Wochen"
    url: str | None = None

    @property
    def severity(self) -> int:
        return STATUS_SEVERITY.get((self.status or "").strip(), 3)


@dataclass
class LineupSlot:
    """Ein Positionsplatz in der voraussichtlichen Aufstellung.

    Ligainsider zeigt je Platz einen projizierten Startelfspieler und optional
    Konkurrenten um denselben Platz.
    """

    row: int  # 1 = Tor, dann aufsteigend Richtung Sturm
    starter: str | None
    alternatives: list[str] = field(default_factory=list)


@dataclass
class ProbableLineup:
    club: str
    slots: list[LineupSlot] = field(default_factory=list)

    @property
    def starters(self) -> list[str]:
        return [s.starter for s in self.slots if s.starter]

    def role_of(self, player_name: str) -> str | None:
        """"Startelf", "Alternative" oder None (gar nicht in der Grafik)."""
        key = normalize_name(player_name)
        for slot in self.slots:
            if slot.starter and normalize_name(slot.starter) == key:
                return "Startelf"
        for slot in self.slots:
            if any(normalize_name(a) == key for a in slot.alternatives):
                return "Alternative"
        return None


@dataclass
class TeamNews:
    club: str
    headlines: list[str] = field(default_factory=list)
    lineup: str | None = None  # zuletzt gemeldete Aufstellung als Rohtext
    probable: ProbableLineup | None = None  # voraussichtliche Aufstellung (Grafik)


@dataclass
class LigainsiderData:
    injuries: list[InjuryEntry] = field(default_factory=list)
    team_news: dict[str, TeamNews] = field(default_factory=dict)
    available: bool = True
    error: str | None = None

    def by_player_key(self) -> dict[str, InjuryEntry]:
        """Verletzungseinträge nach normalisiertem Nachnamen indiziert."""
        out: dict[str, InjuryEntry] = {}
        for entry in self.injuries:
            key = normalize_name(entry.player)
            # Bei Namensgleichheit den schwereren Status behalten.
            if key not in out or entry.severity > out[key].severity:
                out[key] = entry
        return out


def normalize_name(name: str) -> str:
    """Normalisiert einen Spielernamen für den Abgleich zwischen Kickbase und Ligainsider.

    Kickbase liefert im Kader oft nur den Nachnamen ("Schlotterbeck"), Ligainsider den
    vollen Namen ("Nico Schlotterbeck"). Verglichen wird deshalb über den Nachnamen,
    kleingeschrieben und ohne Diakritika.
    """
    cleaned = unicodedata.normalize("NFKD", name or "")
    cleaned = "".join(c for c in cleaned if not unicodedata.combining(c))
    cleaned = re.sub(r"[^\w\s-]", " ", cleaned, flags=re.UNICODE)
    parts = [p for p in cleaned.strip().lower().split() if p]
    if not parts:
        return ""
    return parts[-1]


class LigainsiderClient:
    def __init__(self, session: requests.Session | None = None, delay: float = REQUEST_DELAY):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.delay = delay

    def _get(self, path: str) -> str:
        url = path if path.startswith("http") else f"{BASE_URL}{path}"
        resp = self.session.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        time.sleep(self.delay)
        return resp.text

    def fetch_injuries(self) -> list[InjuryEntry]:
        """Verletzte und gesperrte Spieler der gesamten Liga."""
        from bs4 import BeautifulSoup

        html = self._get("/bundesliga/verletzte-und-gesperrte-spieler/")
        soup = BeautifulSoup(html, "lxml")

        entries: list[InjuryEntry] = []
        # Je Verein ein Block "personal_table" mit Titel und Zeilen.
        for block in soup.select("div.personal_table"):
            title_el = block.select_one("div.leg_table_title h2")
            club = title_el.get_text(" ", strip=True) if title_el else None
            for row in block.select("div.small_table_row"):
                name_el = row.select_one("div.small_table_column1 strong")
                if not name_el:
                    continue
                link = row.select_one("div.small_table_column1 a")
                icon = row.select_one("div.small_table_column1 img")
                col2 = row.select_one("div.small_table_column2")
                col3 = row.select_one("div.small_table_column3")
                col4 = row.select_one("div.small_table_column4")

                def _text(el: Any) -> str | None:
                    if el is None:
                        return None
                    txt = " ".join(el.get_text(" ", strip=True).split())
                    return txt.replace("\xad", "") or None

                entries.append(
                    InjuryEntry(
                        player=name_el.get_text(strip=True),
                        club=club,
                        status=(icon.get("alt").strip() if icon and icon.get("alt") else None),
                        reason=_text(col2),
                        news=_text(col3),
                        since=_text(col4),
                        url=(link.get("href") if link else None),
                    )
                )
        return entries

    def fetch_probable_lineup(self, club: str, path: str) -> ProbableLineup:
        """Voraussichtliche Aufstellung aus der Aufstellungsgrafik der Vereinsseite.

        In der Grafik steht je Positionsplatz der projizierte Startelfspieler sichtbar
        (`sub_child` mit `display: block` bzw. ganz ohne `sub_child`-Container), während
        Konkurrenten um denselben Platz ausgeblendet danebenliegen (`display: none`).
        Genau diese Unterscheidung ist der Kern: Ein Spieler, den Ligainsider nur als
        Alternative führt, ist eben *nicht* für die Startelf vorgesehen.
        """
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(self._get(path), "lxml")
        lineup = ProbableLineup(club=club)
        pitch = soup.select_one("div.stadium_container_bg")
        if pitch is None:
            return lineup

        for row_index, row in enumerate(pitch.select("div.player_position_row"), start=1):
            for column in row.select("div.player_position_column"):
                children = column.select("div.sub_child")
                if not children:
                    name_el = column.select_one("div.player_name")
                    if name_el:
                        lineup.slots.append(
                            LineupSlot(row=row_index, starter=name_el.get_text(strip=True))
                        )
                    continue

                starter: str | None = None
                alternatives: list[str] = []
                for child in children:
                    name_el = child.select_one("div.player_name")
                    if not name_el:
                        continue
                    name = name_el.get_text(strip=True)
                    style = (child.get("style") or "").replace(" ", "").lower()
                    if "display:none" in style:
                        alternatives.append(name)
                    else:
                        starter = starter or name
                if starter or alternatives:
                    lineup.slots.append(
                        LineupSlot(row=row_index, starter=starter, alternatives=alternatives)
                    )
        return lineup

    def fetch_team_news(self, club: str, path: str) -> TeamNews:
        """News-Schlagzeilen und zuletzt gemeldete Aufstellung eines Vereins."""
        from bs4 import BeautifulSoup

        html = self._get(f"{path.rstrip('/')}/verein/news/")
        soup = BeautifulSoup(html, "lxml")

        headlines: list[str] = []
        lineup: str | None = None
        slug = path.strip("/").split("/")[0]
        for a in soup.select(f'a[href*="/{slug}/"]'):
            text = " ".join(a.get_text(" ", strip=True).split()).replace("\xad", "")
            if not text or len(text) < 20 or text in headlines:
                continue
            # Die Aufstellungszeile besteht aus mit Gedankenstrichen getrennten Namen.
            if text.count("–") >= 2 or text.count(" - ") >= 3:
                if lineup is None:
                    lineup = text
                continue
            headlines.append(text)

        return TeamNews(club=club, headlines=headlines[:12], lineup=lineup)


def collect(
    clubs: list[str] | None = None,
    client: LigainsiderClient | None = None,
    with_team_news: bool = True,
) -> LigainsiderData:
    """Sammelt Ligainsider-Daten; Fehler machen die Analyse nicht kaputt, nur ärmer.

    `clubs` schränkt die abgefragten Vereins-News ein (z.B. nur Vereine, die für den
    eigenen Kader und den Transfermarkt relevant sind), um unnötige Requests zu sparen.
    """
    client = client or LigainsiderClient()
    data = LigainsiderData()

    try:
        data.injuries = client.fetch_injuries()
    except Exception as exc:  # Netzwerk, Markup-Änderung, Parser-Fehler
        data.available = False
        data.error = f"Verletzungsliste nicht abrufbar: {exc}"
        return data

    if not with_team_news:
        return data

    targets = TEAM_PAGES if clubs is None else {c: p for c, p in TEAM_PAGES.items() if c in clubs}
    for club, path in targets.items():
        try:
            news = client.fetch_team_news(club, path)
        except Exception:
            continue  # einzelne Vereinsseite darf fehlschlagen
        try:
            news.probable = client.fetch_probable_lineup(club, path)
        except Exception:
            pass  # Aufstellungsgrafik ist optional
        data.team_news[club] = news
    return data


def role_lookup(data: LigainsiderData) -> dict[str, tuple[str, str]]:
    """Normalisierter Spielername -> (Rolle, Verein) aus allen geladenen Aufstellungen."""
    out: dict[str, tuple[str, str]] = {}
    for club, news in data.team_news.items():
        if not news.probable:
            continue
        for slot in news.probable.slots:
            if slot.starter:
                out[normalize_name(slot.starter)] = ("Startelf", club)
            for alt in slot.alternatives:
                out.setdefault(normalize_name(alt), ("Alternative", club))
    return out
