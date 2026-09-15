---
name: sidequest
description: Seitenfrage aus der laufenden Unterhaltung in einen eigenen Zweig auslagern, damit der Hauptverlauf nicht verbogen wird. Der Zweig wird von einem Subagenten mit eigenem Kontext bearbeitet, das Ergebnis kommt kompakt zurueck und wird protokolliert. Nutze diesen Skill, wenn der Nutzer "Seitenfrage", "Nebenthema", "abzweigen", "das mal separat", "ohne den Faden zu verlieren", "Zweig", "sidequest" sagt, oder /sidequest aufruft. Kann Zweige auflisten, anzeigen, zurueckfuehren (merge), verwerfen und als Diagramm darstellen.
---

# sidequest — Seitenfragen abzweigen

Ziel: Eine Frage, die nicht zum aktuellen Arbeitsfaden gehoert, wird **nicht** im
Hauptverlauf ausdiskutiert. Sie bekommt einen eigenen Zweig, wird dort
beantwortet, und der Hauptverlauf sieht nur eine kurze Zusammenfassung — oder gar
nichts, bis der Nutzer den Zweig zurueckfuehrt.

## Warum das funktioniert

Der eigentliche Mechanismus ist der **Subagent**: er hat seinen eigenen Kontext,
liest dort Dateien, probiert Dinge aus und gibt nur das Ergebnis zurueck. Die
Recherche landet also nicht im Haupttranskript. Die Dateiablage unter
`.claude/sidequests/` macht das Ganze ueber Sessions hinweg haltbar und
rueckfuehrbar.

## Helfer

Alle Zustandsaenderungen laufen ueber `sq.py` im Skill-Verzeichnis — nie die
JSON-Datei von Hand editieren.

```bash
python3 .claude/skills/sidequest/sq.py --help
```

Ablage: `.claude/sidequests/index.json` plus `sq-NNN.md` pro Zweig.
IDs sind tolerant: `3`, `sq3`, `sq-3` und `sq-003` meinen dasselbe.

## Aufrufe

### `/sidequest <frage>` — neuen Zweig anlegen (Default)

1. Titel formulieren: 3–6 Woerter, die die Frage benennen.
2. Zweig anlegen:
   ```bash
   python3 .claude/skills/sidequest/sq.py new --title "<titel>" --question "<frage>"
   ```
   Gibt die ID und den Pfad der Zweigdatei aus.
3. **Subagent starten** (`Agent`-Tool, `subagent_type: "general-purpose"`, oder
   `Explore` wenn es reine Recherche im Code ist). Der Prompt enthaelt:
   - die Frage im Wortlaut,
   - nur den Kontext aus dem Hauptverlauf, den die Frage wirklich braucht —
     ein kurzer Absatz, nicht das halbe Transkript,
   - die Anweisung, die Antwort auf Deutsch und in der Tonalitaet aus `CLAUDE.md`
     zu liefern (direkt, anti-fluff),
   - die Anweisung, das Ergebnis als Markdown nach
     `.claude/sidequests/<id>.answer.md` zu schreiben.
   Lass ihn im Hintergrund laufen, wenn der Nutzer im Hauptfaden weiterarbeiten
   will — genau dafuer ist der Skill da. Nur wenn der naechste Schritt des
   Nutzers direkt von der Antwort abhaengt, `run_in_background: false`.
4. Wenn die Antwort da ist, anhaengen und die Zwischendatei aufraeumen:
   ```bash
   python3 .claude/skills/sidequest/sq.py answer <id> --file .claude/sidequests/<id>.answer.md
   rm .claude/sidequests/<id>.answer.md
   ```
5. Im Hauptverlauf **maximal 3–5 Zeilen** berichten: die Kernaussage plus
   `Vollstaendig: /sidequest show <id>`. Nicht die ganze Antwort einkippen —
   das waere genau das Verbiegen, das der Skill verhindern soll.

Wenn schon ein Zweig offen ist und die neue Frage daraus folgt, haengt sie dort
an: `--parent sq-002`.

### `/sidequest list [offen|merged|verworfen]`

```bash
python3 .claude/skills/sidequest/sq.py list --status all
```
Statuswerte: `open`, `merged`, `dropped`, `all`. Ausgabe unveraendert zeigen.

### `/sidequest show <id>`

```bash
python3 .claude/skills/sidequest/sq.py show <id>
```
Die komplette Zweigdatei. Erst hier landet die Langfassung im Hauptverlauf —
weil der Nutzer sie ausdruecklich angefordert hat.

### `/sidequest merge <id>` — zurueckfuehren

Der Zweig ist erledigt und sein Ergebnis soll den Hauptfaden beeinflussen.

1. `sq.py show <id>` lesen.
2. Entscheiden, was tatsaechlich zurueckfliesst: eine Erkenntnis, eine
   Entscheidung, eine konkrete Aufgabe. Nicht der ganze Zweig.
3. Markieren:
   ```bash
   python3 .claude/skills/sidequest/sq.py merge <id> --note "<was zurueckfliesst>"
   ```
4. Im Hauptverlauf ansagen, was das fuer die laufende Arbeit aendert — und es
   dann auch tun, wenn daraus eine Aufgabe folgt.

Faellt beim Zurueckfuehren eine Architektur- oder Produktentscheidung an, gehoert
sie zusaetzlich nach `DECISIONS.md` — aber nur, wenn der Nutzer das bestaetigt.

### `/sidequest drop <id> [grund]`

```bash
python3 .claude/skills/sidequest/sq.py drop <id> --note "<grund>"
```
`reopen <id>` macht das rueckgaengig.

### `/sidequest map` — grafische Uebersicht

```bash
python3 .claude/skills/sidequest/sq.py map
```
Liefert ein Mermaid-`flowchart`: Hauptverlauf als Wurzel, Zweige als Knoten,
rueckgefuehrte Zweige mit gestrichelter Kante zurueck zum Hauptverlauf. Farben
nach WB-Palette (Navy `#1D3C6E`, Blue `#1A8BC4`, Cyan `#00C8E8`).

- **Im Terminal:** den Mermaid-Block direkt ausgeben — in einem ```mermaid-Fence.
- **Als Bild:** das Ergebnis in eine HTML-Seite legen und per `Artifact`
  veroeffentlichen. Artifacts rendern ```mermaid-Fences nativ, ohne Library.
  Nur machen, wenn der Nutzer eine Grafik will (`/sidequest map --artifact` oder
  entsprechend formuliert) — sonst reicht der Textblock.
- `--all` nimmt verworfene Zweige mit dazu.

## Regeln

- **Der Hauptverlauf bleibt schlank.** Das ist der einzige Zweck des Skills.
  Lange Herleitungen bleiben im Subagenten und in der Zweigdatei.
- **Nie ungefragt mergen.** Ein Zweig aendert den Hauptfaden erst, wenn der
  Nutzer `merge` sagt.
- **Keine Code-Aenderungen aus einem Zweig heraus**, ausser der Nutzer fordert es
  ausdruecklich. Ein Zweig beantwortet, er baut nicht um.
- Ergebnisse aus Subagenten sind Vorschlaege, keine Fakten — bei ueberraschenden
  Aussagen gegen den Code oder die Doku gegenpruefen, bevor sie im Hauptverlauf
  als gesichert dargestellt werden.
