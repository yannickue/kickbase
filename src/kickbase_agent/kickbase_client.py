"""Client für die inoffizielle Kickbase-API (v4).

Kickbase veröffentlicht keine offizielle Public-API. Die hier verwendeten Endpunkte und
Feldnamen sind reverse-engineered (wie bei vergleichbaren Community-Projekten) und können
sich jederzeit ändern. Die `_pick`-Hilfsfunktion probiert deshalb mehrere bekannte
Kandidaten-Keys pro Feld durch, statt sich auf einen einzigen exakten Namen zu verlassen.

Nutze `--dump-raw` (siehe main.py), um die rohen JSON-Antworten zu inspizieren, falls ein
Feld nicht (mehr) gefunden wird.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

BASE_URL = "https://api.kickbase.com"
TIMEOUT = 15


class KickbaseError(RuntimeError):
    """Fehler bei der Kommunikation mit der Kickbase-API."""


class KickbaseAuthError(KickbaseError):
    """Login fehlgeschlagen (falsche Zugangsdaten oder API-Änderung)."""


def _pick(data: dict[str, Any], *candidates: str, default: Any = None) -> Any:
    """Gibt den ersten vorhandenen Wert aus `data` für die gegebenen Kandidaten-Keys zurück."""
    for key in candidates:
        if key in data and data[key] is not None:
            return data[key]
    return default


@dataclass
class Player:
    id: str
    name: str
    position: str | None
    team: str | None
    market_value: float | None
    market_value_trend: str | None
    points: float | None
    status: str | None  # z.B. verletzt, gesperrt, fit
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "Player":
        first = _pick(raw, "fn", "firstName", default="")
        last = _pick(raw, "n", "ln", "lastName", "name", default="")
        name = f"{first} {last}".strip() or _pick(raw, "n", "name", default="Unbekannt")
        return cls(
            id=str(_pick(raw, "i", "id", "playerId", default="")),
            name=name,
            position=_pick(raw, "pos", "position", "p", default=None),
            team=_pick(raw, "tn", "teamName", "team", default=None),
            market_value=_pick(raw, "mv", "marketValue", default=None),
            market_value_trend=_pick(raw, "mvt", "marketValueTrend", "trend", default=None),
            points=_pick(raw, "tp", "totalPoints", "points", "p", default=None),
            status=_pick(raw, "st", "status", "injuryStatus", default=None),
            raw=raw,
        )


@dataclass
class MarketOffer:
    player: Player
    price: float | None
    expires_at: str | None
    seller: str | None  # None = vom Kickbase-Bot / neutraler Markt
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "MarketOffer":
        return cls(
            player=Player.from_raw(raw),
            price=_pick(raw, "prc", "price", "mv", default=None),
            expires_at=_pick(raw, "exs", "expiry", "expiresAt", default=None),
            seller=_pick(raw, "selN", "sellerName", "seller", default=None),
            raw=raw,
        )


@dataclass
class LeagueSummary:
    id: str
    name: str
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "LeagueSummary":
        return cls(
            id=str(_pick(raw, "i", "id", default="")),
            name=_pick(raw, "n", "name", default="Unbekannte Liga"),
            raw=raw,
        )


class KickbaseClient:
    def __init__(self, session: requests.Session | None = None, dump_dir: str | Path | None = None):
        self.session = session or requests.Session()
        self.token: str | None = None
        self.user_id: str | None = None
        self.dump_dir = Path(dump_dir) if dump_dir else None
        if self.dump_dir:
            self.dump_dir.mkdir(parents=True, exist_ok=True)

    # -- interne Helfer -----------------------------------------------------

    def _dump(self, name: str, payload: Any) -> None:
        if not self.dump_dir:
            return
        path = self.dump_dir / f"{name}.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = f"{BASE_URL}{path}"
        try:
            resp = self.session.request(
                method, url, headers=self._headers(), timeout=TIMEOUT, **kwargs
            )
        except requests.RequestException as exc:
            raise KickbaseError(f"Netzwerkfehler bei {method} {path}: {exc}") from exc

        if resp.status_code == 401:
            raise KickbaseAuthError(
                "Kickbase hat die Anfrage abgelehnt (401). Zugangsdaten prüfen oder Token abgelaufen."
            )
        if not resp.ok:
            raise KickbaseError(
                f"Kickbase-API-Fehler {resp.status_code} bei {method} {path}: {resp.text[:500]}"
            )
        try:
            return resp.json()
        except ValueError as exc:
            raise KickbaseError(f"Antwort von {path} war kein gültiges JSON") from exc

    # -- öffentliche API ------------------------------------------------------

    def login(self, email: str, password: str) -> None:
        payload = {"em": email, "email": email, "pass": password, "password": password, "loy": False}
        data = self._request("POST", "/v4/user/login", json=payload)
        self._dump("login", data)
        token = _pick(data, "tkn", "token", "accessToken")
        if not token:
            raise KickbaseAuthError(
                "Login-Antwort enthielt keinen Token. Zugangsdaten falsch oder API-Format geändert "
                "(siehe --dump-raw)."
            )
        self.token = token
        user = _pick(data, "u", "user", default={}) or {}
        self.user_id = str(_pick(user, "id", "i", default="")) or None

    def get_leagues(self) -> list[LeagueSummary]:
        data = self._request("GET", "/v4/leagues")
        self._dump("leagues", data)
        items = _pick(data, "it", "items", "leagues", default=[]) or []
        return [LeagueSummary.from_raw(item) for item in items]

    def get_budget(self, league_id: str) -> dict[str, Any]:
        data = self._request("GET", f"/v4/leagues/{league_id}/me")
        self._dump(f"league_{league_id}_me", data)
        return {
            "budget": _pick(data, "b", "budget", default=None),
            "team_value": _pick(data, "tv", "teamValue", default=None),
            "placement": _pick(data, "pl", "placement", "rank", default=None),
            "raw": data,
        }

    def get_squad(self, league_id: str) -> list[Player]:
        data = self._request("GET", f"/v4/leagues/{league_id}/squad")
        self._dump(f"league_{league_id}_squad", data)
        items = _pick(data, "players", "it", "items", default=[]) or []
        return [Player.from_raw(item) for item in items]

    def get_market(self, league_id: str) -> list[MarketOffer]:
        data = self._request("GET", f"/v4/leagues/{league_id}/market")
        self._dump(f"league_{league_id}_market", data)
        items = _pick(data, "it", "items", "market", default=[]) or []
        return [MarketOffer.from_raw(item) for item in items]

    def get_table(self, league_id: str) -> list[dict[str, Any]]:
        data = self._request("GET", f"/v4/leagues/{league_id}/table")
        self._dump(f"league_{league_id}_table", data)
        items = _pick(data, "us", "it", "items", "table", default=[]) or []
        return [
            {
                "team_name": _pick(item, "tn", "teamName", "name", default="?"),
                "rank": _pick(item, "pl", "placement", "rank", default=None),
                "points": _pick(item, "sp", "points", default=None),
            }
            for item in items
        ]


def resolve_league_id(client: KickbaseClient, explicit_league_id: str | None) -> str:
    """Bestimmt die zu verwendende Liga-ID.

    Nimmt eine explizit übergebene ID (CLI-Flag oder Env-Var), sonst wird versucht, die
    einzige Liga automatisch zu erkennen. Bei mehreren Ligen ohne explizite Auswahl wird ein
    Fehler geworfen, damit nichts Falsches ausgewertet wird.
    """
    if explicit_league_id:
        return explicit_league_id

    leagues = client.get_leagues()
    if not leagues:
        raise KickbaseError("Keine Ligen für diesen Account gefunden.")
    if len(leagues) == 1:
        return leagues[0].id

    names = ", ".join(f"{l.name} ({l.id})" for l in leagues)
    raise KickbaseError(
        f"Mehrere Ligen gefunden: {names}. Bitte mit --league-id oder KICKBASE_LEAGUE_ID auswählen."
    )
