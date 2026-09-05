"""Client für die Kickbase-API (v4).

Kickbase veröffentlicht keine offizielle Public-API. Endpunkte und Response-Feldnamen in
diesem Modul stammen aus der Community-Dokumentation
https://github.com/kevinskyba/kickbase-api-doc (Swagger-Spec + Postman-Collection mit
echten Beispiel-Antworten). Das deckt Pfade, Pflichtfelder und die meisten Feldnamen ab.

Zwei Dinge sind dort NICHT dokumentiert und daher best-effort:
- Die Bedeutung der numerischen Status-Codes (`st`) und Positions-Codes (`pos`) — die Werte
  in `STATUS_LABELS`/`POSITION_LABELS` sind aus verbreitetem Community-Wissen übernommen,
  nicht aus der offiziellen Spec.
- Das genaue Feld-Schema der Transfermarkt-Einträge (`/market` liefert `it` ohne
  Typ-Angabe in der Spec). Es wird angenommen, dass ein Markt-Eintrag dieselben
  Spieler-Felder wie ein Kader-Eintrag trägt, plus `prc` (Preis) — das folgt aus dem
  dokumentierten POST-Body beim Einstellen eines Spielers (`{"pi": ..., "prc": ...}`).

Nutze `--dump-raw`, um die rohen JSON-Antworten zu inspizieren, falls ein Feld nicht (mehr)
gefunden wird oder ein Wert unplausibel aussieht.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

BASE_URL = "https://api.kickbase.com"
TIMEOUT = 15

# Bundesliga-Wettbewerbs-ID, wie sie in Liga-Daten als "cpi" auftaucht.
DEFAULT_COMPETITION_ID = "1"

POSITION_LABELS = {1: "Torwart", 2: "Abwehr", 3: "Mittelfeld", 4: "Sturm"}

# Community-bekannte Bedeutung der Status-Codes (nicht offiziell dokumentiert).
STATUS_LABELS = {
    0: "fit",
    1: "angeschlagen",
    2: "verletzt",
    4: "rote Karte / gesperrt",
    8: "gelb-rote Karte",
    16: "im Aufbautraining",
    32: "nicht im Kader",
}


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


def describe_position(code: Any) -> str | None:
    if code is None:
        return None
    try:
        code = int(code)
    except (TypeError, ValueError):
        return str(code)
    return POSITION_LABELS.get(code, f"Position-Code {code}")


def describe_status(code: Any) -> str | None:
    if code is None:
        return None
    try:
        code = int(code)
    except (TypeError, ValueError):
        return str(code)
    return STATUS_LABELS.get(code, f"Status-Code {code} (unbekannt)")


@dataclass
class Player:
    id: str
    name: str
    position: str | None  # Klartext-Label, z.B. "Mittelfeld"
    position_code: int | None
    team_id: str | None
    team: str | None  # wird separat via Team-Namen-Lookup aufgelöst, sonst None
    market_value: float | None
    market_value_change: float | None  # Gesamtveränderung in Euro (Kickbase-Feld "mvgl")
    points: float | None  # Saison-Gesamtpunkte ("p")
    average_points: float | None  # Punkteschnitt ("ap")
    status: str | None  # Klartext-Label, z.B. "verletzt"
    status_code: int | None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "Player":
        pos_code = _pick(raw, "pos", "position", default=None)
        st_code = _pick(raw, "st", "status", default=None)
        return cls(
            id=str(_pick(raw, "i", "pi", "id", "playerId", default="")),
            name=_pick(raw, "n", "name", default="Unbekannt"),
            position=describe_position(pos_code),
            position_code=pos_code,
            team_id=_pick(raw, "tid", "teamId", default=None),
            team=None,
            market_value=_pick(raw, "mv", "marketValue", default=None),
            market_value_change=_pick(raw, "mvgl", default=None),
            points=_pick(raw, "p", "tp", "totalPoints", "points", default=None),
            average_points=_pick(raw, "ap", "averagePoints", default=None),
            status=describe_status(st_code),
            status_code=st_code,
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
        payload = {"em": email, "pass": password, "loy": False}
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
        items = _pick(data, "lins", "it", "items", "leagues", default=[]) or []
        return [LeagueSummary.from_raw(item) for item in items]

    def get_me(self, league_id: str) -> dict[str, Any]:
        """Budget + Liganame für den eingeloggten Nutzer in dieser Liga."""
        data = self._request("GET", f"/v4/leagues/{league_id}/me")
        self._dump(f"league_{league_id}_me", data)
        return {
            "budget": _pick(data, "b", "budget", default=None),
            "league_name": _pick(data, "lnm", "leagueName", default=None),
            "raw": data,
        }

    def get_squad(self, league_id: str) -> list[Player]:
        data = self._request("GET", f"/v4/leagues/{league_id}/squad")
        self._dump(f"league_{league_id}_squad", data)
        items = _pick(data, "it", "players", "items", default=[]) or []
        return [Player.from_raw(item) for item in items]

    def get_market(self, league_id: str) -> tuple[list[MarketOffer], int | None]:
        """Gibt (Marktangebote, aktuelle Spieltag-Nummer) zurück."""
        data = self._request("GET", f"/v4/leagues/{league_id}/market")
        self._dump(f"league_{league_id}_market", data)
        items = _pick(data, "it", "items", "market", default=[]) or []
        day = _pick(data, "day", default=None)
        return [MarketOffer.from_raw(item) for item in items], day

    def get_ranking(self, league_id: str, day_number: int | str) -> list[dict[str, Any]]:
        """Liga-Tabelle für einen Spieltag (dayNumber ist bei Kickbase ein Pflichtparameter)."""
        data = self._request(
            "GET", f"/v4/leagues/{league_id}/ranking", params={"dayNumber": str(day_number)}
        )
        self._dump(f"league_{league_id}_ranking", data)
        items = _pick(data, "us", "items", default=[]) or []
        return [
            {
                "user_id": str(_pick(item, "i", "id", default="")),
                "name": _pick(item, "n", "name", default="?"),
                "team_value": _pick(item, "tv", default=None),
                "season_points": _pick(item, "sp", default=None),
                "matchday_points": _pick(item, "mdp", default=None),
                "season_rank": _pick(item, "spl", default=None),
                "matchday_rank": _pick(item, "mdpl", default=None),
            }
            for item in items
        ]

    def get_team_names(self, competition_id: str = DEFAULT_COMPETITION_ID) -> dict[str, str]:
        """Team-ID -> Team-Name Mapping (z.B. für die Bundesliga, competition_id="1")."""
        data = self._request("GET", f"/v4/competitions/{competition_id}/table")
        self._dump(f"competition_{competition_id}_table", data)
        items = _pick(data, "it", "items", default=[]) or []
        return {str(_pick(item, "tid", default="")): _pick(item, "tn", "name", default="?") for item in items}


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
