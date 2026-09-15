---
name: sidequest
description: Seitenfrage aus der laufenden Unterhaltung in einen eigenen Zweig auslagern, damit der Hauptverlauf nicht verbogen wird. Das Ergebnis kommt kompakt zurueck und wird protokolliert. Funktioniert in Claude Code (mit Dateiablage und Subagent) genauso wie im normalen Chat (als mitgefuehrte Zweigtafel). Nutze diesen Skill, wenn der Nutzer "Seitenfrage", "Nebenthema", "abzweigen", "das mal separat", "ohne den Faden zu verlieren", "Zweig", "sidequest" sagt, oder /sidequest aufruft. Kann Zweige auflisten, anzeigen, zurueckfuehren (merge), verwerfen und als Karte darstellen.
---

# sidequest — Seitenfragen abzweigen

Ziel: Eine Frage, die nicht zum aktuellen Arbeitsfaden gehoert, wird **nicht** im
Hauptverlauf ausdiskutiert. Sie bekommt einen eigenen Zweig, wird dort
beantwortet, und der Hauptverlauf sieht nur die Kernaussage — oder gar nichts,
bis der Nutzer den Zweig zurueckfuehrt.

---

## Zuerst: Betriebsart bestimmen

Der Skill laeuft in zwei Umgebungen. Entscheide **einmal am Anfang**, still,
ohne den Nutzer zu fragen:

**Modus A — mit Werkzeugen.** Du hast ein Bash-artiges Tool und findest `sq.py`
(siehe unten). Dann: Dateiablage, Subagenten, alles wie beschrieben.

**Modus B — reiner Chat.** Kein Bash, kein Dateizugriff, oder `sq.py` nicht
auffindbar. Dann fuehrst du den Zustand als **Zweigtafel** im Gespraech mit.

Fragt der Nutzer in Modus B nach Dingen, die nur Modus A kann (Ablage-Pfade,
eigene Sessions), sag klar, dass es die hier nicht gibt. Nichts erfinden.

### Modus A: `sq.py` finden

```bash
SQ="python3 $(ls -d "$PWD"/.claude/skills/sidequest/sq.py \
                   ~/.claude/skills/sidequest/sq.py \
                   ~/.claude/skills/synced/*/sidequest/sq.py 2>/dev/null | head -1)"
"$SQ" --help
```

Einmal pro Session aufloesen, danach `$SQ` nutzen. Alle Zustandsaenderungen
laufen darueber — nie die `index.json` von Hand editieren.

Ablage: `<aktuelles Verzeichnis>/.claude/sidequests/` (`index.json` plus
`sq-NNN.md` je Zweig). Zweige gehoeren zum Projekt, in dem sie entstehen, auch
bei global installiertem Skill. `SIDEQUEST_DIR` erzwingt einen anderen Ort.
Zum Skill gehoert ausserdem `karte_template.html`, die Vorlage der Zweigkarte.

Findet die Aufloesung nichts, ist es **Modus B** — nicht raten, nicht nach
einem Pfad suchen.

### Modus B: die Zweigtafel

Der Zustand lebt in der Unterhaltung. Nach **jeder** Aenderung gibst du die
komplette Tafel neu aus — die zuletzt ausgegebene gilt:

```
### Zweigtafel
| ID     | St | Titel                   | Aus    | Kernaussage                    |
|--------|----|-------------------------|--------|--------------------------------|
| sq-001 | ○  | Rate-Limits der Bot-API | main   | 30/s global, 20/min pro Gruppe |
| sq-002 | ●  | bcrypt statt argon2     | main   | bleibt bcrypt, keine Dep       |
| sq-003 | ✕  | argon2 Speicherbedarf   | sq-002 | haengt an sq-002, erledigt     |
```

`○` offen · `●` zurueckgefuehrt · `✕` verworfen. IDs fortlaufend `sq-NNN`,
`Aus` ist `main` oder die Eltern-ID.

Grenzen dieses Modus — **beim ersten Zweig einmal ansagen**, dann nicht mehr:

- Die Tafel ueberlebt die Unterhaltung nicht. Wer sie behalten will, laesst sie
  sich am Ende als Markdown geben.
- Gespeichert wird nur die **Kernaussage**, nicht die Langfassung. `show`
  entfaltet aus dem, was im Gespraech steht — das ist weniger als eine
  Zweigdatei, nicht dasselbe.
- Es gibt keinen Subagenten. Du beantwortest die Frage selbst. Die Disziplin
  ("kurz im Hauptverlauf, Rest bleibt ungeschrieben") traegt hier die ganze
  Arbeit.

---

## Wo laeuft der Zweig — nur in Modus A fragen

Nur wenn `create_session` (claude-code-remote) verfuegbar ist — also in Claude
Code on the web / Remote-Sessions. Dann **vor dem Anlegen** per
`AskUserQuestion` fragen:

- **Subagent (hier)** — empfohlener Default. Eigener Kontext, Ergebnis landet
  direkt in der Zweigdatei, kein Fensterwechsel.
- **Eigene Session (neues Fenster)** — separate Unterhaltung, die der Nutzer in
  einem neuen Tab oeffnet. Sinnvoll bei laengeren Seitenfragen mit eigenen
  Dateiaenderungen.

Nicht fragen, wenn der Nutzer die Variante schon genannt hat, wenn
`create_session` fehlt (lokales CLI, normaler Chat) oder in Modus B. Dann
Subagent bzw. Selbstbeantwortung.

### Eigene Session anlegen

1. Zweig mit `"$SQ" new` anlegen.
2. `create_session` mit `title` = `sidequest <id>: <titel>`, `tags` =
   `["sidequest", "<id>"]` und einem **vollstaendig eigenstaendigen** `prompt`:
   die neue Session sieht diese Unterhaltung nicht.
3. Verknuepfen:
   ```bash
   "$SQ" link <id> --url "https://claude.ai/code/<session_id>" --session-id <session_id>
   ```
4. Link geben und **klar sagen, dass sich das Fenster nicht von selbst oeffnet**.

Die neue Session kann nicht in die Ablage zurueckschreiben — eigener Container.
Das Ergebnis reicht der Nutzer herueber. Nicht anders darstellen.

---

## Aufrufe

### `/sidequest <frage>` — neuen Zweig anlegen (Default)

Titel formulieren: 3–6 Woerter, die die Frage benennen.

**Modus A:**
1. ```bash
   "$SQ" new --title "<titel>" --question "<frage>"
   ```
2. **Subagent starten** (`Agent`, `general-purpose`; `Explore` bei reiner
   Code-Recherche). Der Prompt enthaelt die Frage im Wortlaut, nur den wirklich
   noetigen Kontext (ein Absatz, nicht das halbe Transkript), die Anweisung auf
   Deutsch und im Ton der `CLAUDE.md` zu antworten, und das Ziel
   `.claude/sidequests/<id>.answer.md`. Im Hintergrund laufen lassen, ausser der
   naechste Schritt des Nutzers haengt direkt an der Antwort.
3. ```bash
   "$SQ" answer <id> --file .claude/sidequests/<id>.answer.md
   rm .claude/sidequests/<id>.answer.md
   ```

**Modus B:**
1. Naechste freie ID vergeben, Zweig in die Tafel aufnehmen.
2. Die Frage selbst beantworten — aber **die Herleitung nicht ausbreiten**.
   Denk sie durch, schreib nur das Ergebnis.
3. Kernaussage eintragen, Tafel neu ausgeben.

**Beide Modi, zum Schluss:** im Hauptverlauf **maximal 3–5 Zeilen** berichten —
Kernaussage plus `Vollstaendig: /sidequest show <id>`. Die ganze Antwort
einkippen waere genau das Verbiegen, das der Skill verhindern soll.

Folgt die neue Frage aus einem offenen Zweig, haengt sie dort an
(`--parent sq-002` bzw. Spalte `Aus`).

### `/sidequest list [offen|merged|verworfen]`

- **A:** `"$SQ" list --status all` — Ausgabe unveraendert zeigen.
- **B:** Tafel ausgeben, bei Filter nur die passenden Zeilen.

Statuswerte: `open`, `merged`, `dropped`, `all`.

### `/sidequest show <id>`

- **A:** `"$SQ" show <id>` — die komplette Zweigdatei.
- **B:** die Langfassung zu diesem Zweig entfalten.

Erst hier landet die Langfassung im Hauptverlauf, weil der Nutzer sie
ausdruecklich angefordert hat.

### `/sidequest merge <id>` — zurueckfuehren

1. Zweig lesen (`show`).
2. Entscheiden, was tatsaechlich zurueckfliesst: eine Erkenntnis, eine
   Entscheidung, eine Aufgabe. Nicht der ganze Zweig.
3. **A:** `"$SQ" merge <id> --note "<was zurueckfliesst>"` ·
   **B:** Status auf `●`, Kernaussage auf die Rueckfuehrung setzen, Tafel neu.
4. Im Hauptverlauf ansagen, was das fuer die laufende Arbeit aendert — und es
   dann auch tun, wenn daraus eine Aufgabe folgt.

Faellt dabei eine Architektur- oder Produktentscheidung an, gehoert sie
zusaetzlich nach `DECISIONS.md` — aber nur, wenn der Nutzer das bestaetigt.

### `/sidequest drop <id> [grund]`

- **A:** `"$SQ" drop <id> --note "<grund>"`, `reopen <id>` macht es rueckgaengig.
- **B:** Status auf `✕`, Grund in die Kernaussage, Tafel neu.

### `/sidequest map` — Zweigkarte

**Als Text** (Default):

- **A:** `"$SQ" map` — Ausgabe in einem Mermaid-Fence zeigen. `--all` nimmt
  verworfene Zweige mit.
- **B:** denselben Graphen aus der Tafel schreiben: `flowchart LR`, `main` als
  `(["Hauptverlauf"])`, je Zweig ein Knoten `sq_001["sq-001<br/>Titel"]`, Kante
  vom Elternteil, bei zurueckgefuehrten zusaetzlich
  `sq_001 -.rueckgefuehrt.-> main`. Farben: `open` `fill:#00C8E8`, `merged`
  `fill:#1A8BC4`, `dropped` `fill:#cfd6e0`, Rahmen jeweils `stroke:#1D3C6E`.

**Als Grafik** (bei `--artifact` oder "zeig mir die Karte"):

- **A:** ```bash
  "$SQ" map --html /tmp/zweigkarte.html
  ```
  erzeugt die fertige Seite aus `karte_template.html` — Zweigbaum, Legende,
  Zaehler, ein Datensatz je Zweig, WB-Farben, Hell/Dunkel. Diese Datei
  unveraendert per `Artifact` veroeffentlichen, `favicon: "🌿"`.
- **B:** eine HTML-Seite mit dem Mermaid-Fence bauen und per `Artifact`
  veroeffentlichen. Artifacts rendern Mermaid-Fences nativ, ohne Library.

Die Seite nicht von Hand nachbauen, wenn das Template verfuegbar ist, und das
Template nicht pro Aufruf umschreiben — es ist die eine Stelle, an der das
Aussehen definiert ist. Beim erneuten Veroeffentlichen denselben Dateipfad
nehmen, damit das Artifact an seiner URL aktualisiert wird.

---

## Regeln

- **Der Hauptverlauf bleibt schlank.** Das ist der einzige Zweck des Skills.
  Lange Herleitungen bleiben im Subagenten, in der Zweigdatei — oder werden in
  Modus B gar nicht erst ausgeschrieben.
- **Nie ungefragt mergen.** Ein Zweig aendert den Hauptfaden erst, wenn der
  Nutzer `merge` sagt.
- **Keine Code-Aenderungen aus einem Zweig heraus**, ausser der Nutzer fordert
  es ausdruecklich. Ein Zweig beantwortet, er baut nicht um.
- **In Modus B nach jeder Aenderung die Tafel neu ausgeben.** Eine veraltete
  Tafel weiter oben im Verlauf ist die haeufigste Fehlerquelle.
- Ergebnisse aus Subagenten sind Vorschlaege, keine Fakten — bei ueberraschenden
  Aussagen gegen Code oder Doku gegenpruefen, bevor sie im Hauptverlauf als
  gesichert dargestellt werden.
