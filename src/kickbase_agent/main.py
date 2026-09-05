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


def load_mock_snapshot() -> KickbaseSnapshot:
    leagues = _load_json(FIXTURES_DIR / "leagues.json")["it"]
    me = _load_json(FIXTURES_DIR / "me.json")
    squad_raw = _load_json(FIXTURES_DIR / "squad.json")["players"]
    market_raw = _load_json(FIXTURES_DIR / "market.json")["it"]
    table_raw = _load_json(FIXTURES_DIR / "table.json")["us"]

    return KickbaseSnapshot(
        league_name=leagues[0]["n"],
        budget=me.get("b"),
        team_value=me.get("tv"),
        placement=me.get("pl"),
        squad=[Player.from_raw(p) for p in squad_raw],
        market=[MarketOffer.from_raw(m) for m in market_raw],
        table=[
            {"team_name": row["tn"], "rank": row["pl"], "points": row["sp"]}
            for row in table_raw
        ],
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
    league_name = leagues.get(league_id, f"Liga {league_id}")

    budget_info = client.get_budget(league_id)
    squad = client.get_squad(league_id)
    market = client.get_market(league_id)
    table = client.get_table(league_id)

    return KickbaseSnapshot(
        league_name=league_name,
        budget=budget_info["budget"],
        team_value=budget_info["team_value"],
        placement=budget_info["placement"],
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
