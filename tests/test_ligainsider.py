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


LINEUP_HTML = """
<html><body><div class="stadium_container_bg">
  <div class="player_position_row">
    <div class="player_position_column"><div class="player_name"><a>Zentner</a></div></div>
  </div>
  <div class="player_position_row">
    <div class="player_position_column">
      <div class="sub_child" style="display: block;"><div class="player_name"><a>Gruber</a></div></div>
      <div class="sub_child" style="display: none;"><div class="player_name"><a>Posch</a></div></div>
    </div>
    <div class="player_position_column"><div class="player_name"><a>Potulski</a></div></div>
  </div>
</div></body></html>
"""


def test_probable_lineup_separates_starters_from_alternatives():
    """Ein ausgeblendeter Spieler ist Konkurrent um den Platz, kein Startelfspieler."""
    lineup = _StubClient(LINEUP_HTML).fetch_probable_lineup("Mainz", "/x/17/")

    assert lineup.starters == ["Zentner", "Gruber", "Potulski"]
    assert lineup.role_of("Gruber") == "Startelf"
    assert lineup.role_of("Posch") == "Alternative"
    # Kickbase liefert nur den Nachnamen — der Abgleich muss trotzdem greifen.
    assert lineup.role_of("Stefan Posch") == "Alternative"
    assert lineup.role_of("Irgendwer") is None


def test_role_lookup_indexes_all_loaded_teams():
    from kickbase_agent.ligainsider import LigainsiderData, TeamNews, role_lookup

    lineup = _StubClient(LINEUP_HTML).fetch_probable_lineup("Mainz", "/x/17/")
    data = LigainsiderData(team_news={"Mainz": TeamNews(club="Mainz", probable=lineup)})

    lookup = role_lookup(data)
    assert lookup[normalize_name("Posch")] == ("Alternative", "Mainz")
    assert lookup[normalize_name("Gruber")] == ("Startelf", "Mainz")
