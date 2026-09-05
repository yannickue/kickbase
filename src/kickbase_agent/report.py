"""Formatiert die Claude-Antwort als vollständigen Bericht (Konsole/Datei)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .claude_advisor import KickbaseSnapshot


def build_report(snapshot: KickbaseSnapshot, recommendation: str) -> str:
    header = (
        f"# Kickbase-Bericht — {snapshot.league_name}\n"
        f"_Erstellt am {datetime.now().strftime('%Y-%m-%d %H:%M')}_\n\n"
    )
    return header + recommendation.strip() + "\n"


def write_report(report: str, path: str | Path) -> Path:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    return out_path
