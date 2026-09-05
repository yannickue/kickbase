# Kickbase Agent

Ein Kommandozeilen-Agent, der sich bei deinem [Kickbase](https://www.kickbase.com)-Account
anmeldet, den aktuellen Zustand deines Teams (Kader, Budget, Transfermarkt, Tabelle) abruft
und die Daten von **Claude** analysieren lässt. Am Ende bekommst du einen kompakten,
deutschsprachigen Bericht mit konkreten Handlungsempfehlungen (Transfers, Aufstellung,
Risiken durch Verletzungen/Sperren, Budgetnutzung).

Es kommt bewusst **kein anderes KI-Modell** zum Einsatz — die gesamte Auswertung läuft
über die Anthropic-API (Claude).

## Funktionsweise

```
Kickbase-Login → Kader/Markt/Budget/Tabelle abrufen → Prompt bauen → Claude fragen → Bericht
```

1. `kickbase_client.py` spricht die inoffizielle Kickbase-API (v4) an: Login, Ligen, Kader,
   Transfermarkt, Budget, Tabelle.
2. `claude_advisor.py` baut aus diesen Daten einen strukturierten Prompt und schickt ihn an
   Claude.
3. `report.py` formatiert die Antwort als Markdown-Bericht (Konsole + optionale Datei).
4. `main.py` ist der CLI-Einstiegspunkt.

## Setup

```bash
cd /home/user/kickbase
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Trage in `.env` ein:

```
KICKBASE_EMAIL=deine-email@example.com
KICKBASE_PASSWORD=dein-passwort
ANTHROPIC_API_KEY=sk-ant-...
# optional, falls du in mehreren Ligen spielst:
KICKBASE_LEAGUE_ID=
# optional, Standard ist claude-sonnet-5
ANTHROPIC_MODEL=claude-sonnet-5
```

`.env` liegt in `.gitignore` und wird nie eingecheckt. Speichere dein Passwort nirgendwo
sonst ab.

## Nutzung

```bash
python -m kickbase_agent.main
```

Optionen:

- `--league-id <id>`: explizite Liga wählen (überschreibt `KICKBASE_LEAGUE_ID`), nötig falls
  du in mehreren Ligen spielst und keine ID in `.env` gesetzt ist.
- `--output report.md`: Bericht zusätzlich als Markdown-Datei speichern.
- `--dump-raw <verzeichnis>`: die rohen JSON-Antworten der Kickbase-API zusätzlich in dieses
  Verzeichnis schreiben (hilfreich zum Debuggen, siehe unten).
- `--mock`: nutzt Beispieldaten aus `tests/fixtures/` statt echter Kickbase-Zugangsdaten —
  zum Ausprobieren des Berichts/Prompts ohne Login.

Beispiel:

```bash
python -m kickbase_agent.main --output berichte/$(date +%F).md
```

## Wichtiger Hinweis zur Kickbase-API

Kickbase bietet keine offizielle, dokumentierte Public-API an. Dieses Projekt nutzt die
Endpunkte, die auch die mobile App verwendet (reverse-engineered, wie in etlichen
Community-Projekten üblich). Das bedeutet:

- Kickbase kann Feldnamen oder Endpunkte jederzeit ändern, ohne Vorankündigung.
- Die Feld-Zuordnung in `kickbase_client.py` (`_pick`-Aufrufe mit mehreren Kandidaten-Keys)
  ist entsprechend defensiv geschrieben, kann aber trotzdem mal ins Leere laufen.
- Wenn ein Bericht auffällig leer/falsch aussieht: `--dump-raw ./debug` laufen lassen, die
  JSON-Dateien ansehen und die betroffenen Key-Listen in `kickbase_client.py` ergänzen.
- Nutze nur deinen eigenen Account und beachte die Nutzungsbedingungen von Kickbase. Der
  Agent führt **keine** automatischen Käufe/Verkäufe aus — er gibt nur Empfehlungen. Alles
  Weitere entscheidest du selbst in der App.

## Tests

```bash
pip install -r requirements.txt
python -m pytest tests/
```

Die Tests laufen komplett offline gegen die Fixtures in `tests/fixtures/` (kein echter
Kickbase- oder Anthropic-API-Call).

## Projektstruktur

```
src/kickbase_agent/
  kickbase_client.py   # Kickbase-API-Client (Login, Kader, Markt, Budget, Tabelle)
  claude_advisor.py     # Prompt-Aufbau + Claude-Aufruf
  report.py             # Markdown-Report-Formatierung
  main.py                # CLI
tests/
  fixtures/              # Beispiel-JSON für --mock und Unit-Tests
  test_kickbase_client.py
  test_claude_advisor.py
```
