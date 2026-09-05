"""Tests für die externen Zusatzquellen (Transfermarkt, OpenLigaDB)."""

from datetime import date

from kickbase_agent.openligadb import Fixture, FixtureData, _same_team
from kickbase_agent.transfermarkt import (
    Absence,
    TransfermarktClient,
    _parse_date,
    _split_name_and_position,
)

TM_HTML = """
<html><body>
<table class="items">
  <thead><tr>
    <th>Spieler/Position</th><th>Verein</th><th>Verletzung</th><th>bis ca.</th><th>Marktwert</th>
  </tr></thead>
  <tbody>
    <tr>
      <td><a href="/x">Justin Diehl</a> Linksaußen</td>
      <td><a href="/vfb" title="VfB Stuttgart">VfB</a></td>
      <td>Oberschenkelverletzung</td>
      <td>30.09.2026</td>
      <td>5,00 Mio. €</td>
    </tr>
    <tr>
      <td><a href="/y">Oliver Burke</a> Mittelstürmer</td>
      <td><a href="/hsv" title="Hamburger SV">HSV</a></td>
      <td>Achillessehnenprobleme</td>
      <td></td>
      <td>3,00 Mio. €</td>
    </tr>
  </tbody>
</table>
</body></html>
"""


class _StubTM(TransfermarktClient):
    def _get(self, path: str) -> str:  # type: ignore[override]
        return TM_HTML


def test_transfermarkt_parses_absences_with_return_dates():
    absences = _StubTM().fetch_absences()
    assert len(absences) == 2

    diehl = next(a for a in absences if a.player == "Justin Diehl")
    assert diehl.position == "Linksaußen"
    assert diehl.club == "VfB Stuttgart"
    assert diehl.return_date == date(2026, 9, 30)
    assert diehl.days_until_return(today=date(2026, 9, 20)) == 10

    burke = next(a for a in absences if a.player == "Oliver Burke")
    assert burke.return_date is None
    assert burke.days_until_return() is None


def test_split_name_and_position_handles_glued_cell_text():
    assert _split_name_and_position("Tiago Tomás Rechtsaußen") == ("Tiago Tomás", "Rechtsaußen")
    assert _split_name_and_position("Marvin Friedrich Innenverteidiger") == (
        "Marvin Friedrich",
        "Innenverteidiger",
    )
    # Unbekannte Position: Name bleibt vollständig erhalten.
    assert _split_name_and_position("Nur Ein Name") == ("Nur Ein Name", None)


def test_parse_date_ignores_noise():
    assert _parse_date("bis ca. 11.10.2026") == date(2026, 10, 11)
    assert _parse_date("") is None
    assert _parse_date("unbekannt") is None


def test_team_matching_bridges_kickbase_short_names():
    assert _same_team("Dortmund", "Borussia Dortmund")
    assert _same_team("Bayern", "FC Bayern München")
    assert not _same_team("Dortmund", "FC Bayern München")


def test_next_opponent_reports_home_and_away():
    data = FixtureData(
        matchday=4,
        fixtures=[
            Fixture(4, None, "FC Bayern München", "1. FC Union Berlin"),
            Fixture(4, None, "Borussia Dortmund", "SC Freiburg"),
        ],
    )
    assert data.next_opponent("Bayern") == ("1. FC Union Berlin", True)
    assert data.next_opponent("Freiburg") == ("Borussia Dortmund", False)
    assert data.next_opponent("Hamburg") is None
