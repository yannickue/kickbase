from pathlib import Path

from kickbase_agent.ligainsider import LigainsiderClient, LigainsiderData, normalize_name

FIXTURES = Path(__file__).parent / "fixtures"


class _StubClient(LigainsiderClient):
    """Client, der statt Netzwerk eine gespeicherte Seite liefert."""

    def __init__(self, html: str):
        super().__init__(delay=0)
        self._html = html

    def _get(self, path: str) -> str:  # type: ignore[override]
        return self._html


def test_parses_injury_rows_with_club_and_status():
    html = (FIXTURES / "ligainsider_injuries.html").read_text(encoding="utf-8")
    entries = _StubClient(html).fetch_injuries()

    assert len(entries) == 3
    can = next(e for e in entries if e.player == "Emre Can")
    assert can.club == "Borussia Dortmund"
    assert can.status == "Verletzung"
    assert can.reason == "Kreuzbandriss"
    assert can.since == "6 Monaten und 1 Woche"

    schlotti = next(e for e in entries if "Schlotterbeck" in e.player)
    assert schlotti.status == "Aufbautraining"
    # Weiche Trennstriche aus dem Markup dürfen nicht im Text landen.
    assert "\xad" not in (schlotti.news or "")
    assert "Schlotterbeck absolviert" in (schlotti.news or "")

    gnabry = next(e for e in entries if e.player == "Serge Gnabry")
    assert gnabry.club == "FC Bayern München"


def test_normalize_name_matches_kickbase_short_names():
    # Kickbase liefert im Kader nur den Nachnamen, Ligainsider den vollen Namen.
    assert normalize_name("Nico Schlotterbeck") == normalize_name("Schlotterbeck")
    assert normalize_name("Loïc Badé") == normalize_name("Bade")
    assert normalize_name("") == ""


def test_index_keeps_more_severe_entry_on_name_clash():
    html = (FIXTURES / "ligainsider_injuries.html").read_text(encoding="utf-8")
    entries = _StubClient(html).fetch_injuries()
    data = LigainsiderData(injuries=entries)
    index = data.by_player_key()
    assert index[normalize_name("Schlotterbeck")].status == "Aufbautraining"
    assert index[normalize_name("Emre Can")].severity == 5
