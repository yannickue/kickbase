"""CLI-Einstiegspunkt: Kickbase-Daten holen, Claude fragen, Bericht ausgeben."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from .claude_advisor import DEFAULT_MODEL, KickbaseSnapshot, get_recommendation
from .kickbase_client import (
    DEFAULT_COMPETITION_ID,
    KickbaseClient,
    KickbaseError,
    MarketOffer,
    Player,
    resolve_league_id,
)
from .report import build_report, write_report

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Kickbase-Agent: Claude-basierte Kaderberatung")
    parser.add_argument("--league-id", help="Liga-ID (überschreibt KICKBASE_LEAGUE_ID)")
    parser.add_argument("--output", help="Bericht zusätzlich als Markdown-Datei speichern")
    parser.add_argument(
        "--dump-raw", metavar="DIR", help="Rohe Kickbase-JSON-Antworten in dieses Verzeichnis schreiben"
    )
    parser.add_argument(
        "--mock", action="store_true", help="Beispieldaten aus tests/fixtures/ statt echtem Login nutzen"
    )
    return parser.parse_args(argv)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _apply_team_names(players: list[Player], team_names: dict[str, str]) -> None:
    for p in players:
        if p.team_id and p.team_id in team_names:
            p.team = team_names[p.team_id]


def load_mock_snapshot() -> KickbaseSnapshot:
    leagues = _load_json(FIXTURES_DIR / "leagues.json")["lins"]
    me = _load_json(FIXTURES_DIR / "me.json")
    squad_raw = _load_json(FIXTURES_DIR / "squad.json")["it"]
    market_raw = _load_json(FIXTURES_DIR / "market.json")
    ranking_raw = _load_json(FIXTURES_DIR / "ranking.json")["us"]
    team_table_raw = _load_json(FIXTURES_DIR / "team_table.json")["it"]

    squad = [Player.from_raw(p) for p in squad_raw]
    market = [MarketOffer.from_raw(m) for m in market_raw["it"]]
    team_names = {t["tid"]: t["tn"] for t in team_table_raw}
    _apply_team_names(squad, team_names)
    _apply_team_names([o.player for o in market], team_names)

    table = [
        {
            "user_id": row["i"],
            "name": row["n"],
            "team_value": row.get("tv"),
            "season_points": row.get("sp"),
            "season_rank": row.get("spl"),
        }
        for row in ranking_raw
    ]
    own = next((r for r in table if r["user_id"] == "mock-user-1"), None)

    return KickbaseSnapshot(
        league_name=me.get("lnm") or leagues[0]["n"],
        budget=me.get("b"),
        team_value=own["team_value"] if own else None,
        placement=own["season_rank"] if own else None,
        squad=squad,
        market=market,
        table=table,
    )


def load_live_snapshot(league_id_arg: str | None, dump_dir: str | None) -> KickbaseSnapshot:
    email = os.environ.get("KICKBASE_EMAIL")
    password = os.environ.get("KICKBASE_PASSWORD")
    if not email or not password:
        raise KickbaseError(
            "KICKBASE_EMAIL / KICKBASE_PASSWORD nicht gesetzt (siehe .env.example)."
        )

    client = KickbaseClient(dump_dir=dump_dir)
    client.login(email, password)

    league_id = league_id_arg or os.environ.get("KICKBASE_LEAGUE_ID")
    league_id = resolve_league_id(client, league_id)

    leagues = {l.id: l.name for l in client.get_leagues()}
    me = client.get_me(league_id)
    league_name = me["league_name"] or leagues.get(league_id, f"Liga {league_id}")

    squad = client.get_squad(league_id)
    market, current_day = client.get_market(league_id)

    table: list[dict] = []
    team_value = None
    placement = None
    if current_day is not None:
        table = client.get_ranking(league_id, current_day)
        own = next((r for r in table if r["user_id"] == client.user_id), None)
        if own:
            team_value = own["team_value"]
            placement = own["season_rank"]

    competition_id = me["raw"].get("cpi", DEFAULT_COMPETITION_ID)
    team_names = client.get_team_names(competition_id)
    _apply_team_names(squad, team_names)
    _apply_team_names([o.player for o in market], team_names)

    return KickbaseSnapshot(
        league_name=league_name,
        budget=me["budget"],
        team_value=team_value,
        placement=placement,
        squad=squad,
        market=market,
        table=table,
    )


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Fehler: ANTHROPIC_API_KEY nicht gesetzt (siehe .env.example).", file=sys.stderr)
        return 1
    model = os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)

    try:
        if args.mock:
            snapshot = load_mock_snapshot()
        else:
            snapshot = load_live_snapshot(args.league_id, args.dump_raw)
    except KickbaseError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1

    recommendation = get_recommendation(snapshot, api_key=api_key, model=model)
    report = build_report(snapshot, recommendation)

    print(report)

    if args.output:
        out_path = write_report(report, args.output)
        print(f"\nBericht gespeichert unter: {out_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
