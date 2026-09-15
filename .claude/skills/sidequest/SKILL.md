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

## Helfer finden

Alle Zustandsaenderungen laufen ueber `sq.py` aus diesem Skill-Verzeichnis —
nie die JSON-Datei von Hand editieren.

Der Skill kann projektlokal, rechnerweit oder ueber den Account-Sync
installiert sein. **Loese den Pfad einmal pro Session auf** und nutze danach
`$SQ`:

```bash
SQ="python3 $(ls -d "$PWD"/.claude/skills/sidequest/sq.py \
                   ~/.claude/skills/sidequest/sq.py \
                   ~/.claude/skills/synced/*/sidequest/sq.py 2>/dev/null | head -1)"
"$SQ" --help
```

Findet das nichts, ist der Skill nicht korrekt installiert — sag das dem
Nutzer, statt einen Pfad zu raten.

**Ablage:** `<aktuelles Verzeichnis>/.claude/sidequests/` — Zweige gehoeren
zu dem Projekt, in dem sie entstehen, auch bei global installiertem Skill.
`index.json` plus `sq-NNN.md` pro Zweig. Mit der Umgebungsvariable
`SIDEQUEST_DIR` laesst sich ein anderer Ort erzwingen.

IDs sind tolerant: `3`, `sq3`, `sq-3` und `sq-003` meinen dasselbe.

Neben der SKILL.md und `sq.py` gehoert `karte_template.html` zum Skill —
die Vorlage fuer die grafische Zweigkarte.

## Wo laeuft der Zweig — vorher fragen

Bevor du einen Zweig anlegst, **frag mit `AskUserQuestion` nach**, wie er laufen
soll. Eine Frage, zwei Optionen:

- **Subagent (hier)** — empfohlener Default. Laeuft in dieser Unterhaltung,
  hat den Kontext des Hauptverlaufs, das Ergebnis landet direkt in der
  Zweigdatei. Kein Fensterwechsel.
- **Eigene Session (neues Fenster)** — eine separate Unterhaltung, die der
  Nutzer in einem neuen Tab oeffnet. Sinnvoll, wenn die Seitenfrage laenger
  wird, eigene Dateiaenderungen braucht oder der Nutzer parallel weiterarbeiten
  will.

Ausnahmen, in denen du **nicht** fragst: der Nutzer hat die Variante schon
genannt ("mach das in einer eigenen Session"), oder es laeuft keine
Nachfrage-Moeglichkeit — dann nimm den Subagenten.

### Eigene Session anlegen

Nur moeglich, wenn das Tool `create_session` (claude-code-remote) vorhanden
ist — also in Claude Code on the web / Remote-Sessions. **Im lokalen CLI gibt
es das nicht**; dort sag das offen und nimm den Subagenten, statt etwas zu
versprechen, was nicht geht.

1. Zweig wie ueblich mit `"$SQ" new` anlegen.
2. `create_session` aufrufen mit:
   - `title`: `sidequest <id>: <titel>`
   - `prompt`: **vollstaendig eigenstaendig** — die neue Session sieht diese
     Unterhaltung nicht. Also: die Frage im Wortlaut, der noetige Kontext in
     ein paar Saetzen, und der Hinweis, dass das Ergebnis am Ende kompakt
     zusammengefasst werden soll.
   - `tags`: `["sidequest", "<id>"]`
3. Die zurueckgegebene Session mit dem Zweig verknuepfen:
   ```bash
   "$SQ" link <id> --url "https://claude.ai/code/<session_id>" --session-id <session_id>
   ```
4. Dem Nutzer den Link geben. **Sag dazu klar, dass sich das Fenster nicht von
   selbst oeffnet** — er klickt den Link, der Tab geht auf.

Die neue Session kann **nicht** in die Zweigdatei zurueckschreiben: sie laeuft
in einem eigenen Container ohne Zugriff auf diese Ablage. Das Ergebnis kommt
zurueck, indem der Nutzer es herueberreicht oder du es beim Zurueckfuehren
abfragst. Stell das nicht anders dar.

## Aufrufe

### `/sidequest <frage>` — neuen Zweig anlegen (Default)

1. Titel formulieren: 3–6 Woerter, die die Frage benennen.
2. Zweig anlegen:
   ```bash
   "$SQ" new --title "<titel>" --question "<frage>"
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
   "$SQ" answer <id> --file .claude/sidequests/<id>.answer.md
   rm .claude/sidequests/<id>.answer.md
   ```
5. Im Hauptverlauf **maximal 3–5 Zeilen** berichten: die Kernaussage plus
   `Vollstaendig: /sidequest show <id>`. Nicht die ganze Antwort einkippen —
   das waere genau das Verbiegen, das der Skill verhindern soll.

Wenn schon ein Zweig offen ist und die neue Frage daraus folgt, haengt sie dort
an: `--parent sq-002`.

### `/sidequest list [offen|merged|verworfen]`

```bash
"$SQ" list --status all
```
Statuswerte: `open`, `merged`, `dropped`, `all`. Ausgabe unveraendert zeigen.

### `/sidequest show <id>`

```bash
"$SQ" show <id>
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
   "$SQ" merge <id> --note "<was zurueckfliesst>"
   ```
4. Im Hauptverlauf ansagen, was das fuer die laufende Arbeit aendert — und es
   dann auch tun, wenn daraus eine Aufgabe folgt.

Faellt beim Zurueckfuehren eine Architektur- oder Produktentscheidung an, gehoert
sie zusaetzlich nach `DECISIONS.md` — aber nur, wenn der Nutzer das bestaetigt.

### `/sidequest drop <id> [grund]`

```bash
"$SQ" drop <id> --note "<grund>"
```
`reopen <id>` macht das rueckgaengig.

### `/sidequest map` — Zweigkarte

**Als Text** (Default, im Terminal):

```bash
"$SQ" map
```
Mermaid-`flowchart` in einem ```mermaid-Fence ausgeben. Hauptverlauf als Wurzel,
Zweige als Knoten, rueckgefuehrte Zweige mit gestrichelter Kante zurueck.
`--all` nimmt verworfene Zweige mit.

**Als Grafik** (wenn der Nutzer eine Grafik will, `--artifact`, "zeig mir die
Karte" o.ae.):

```bash
"$SQ" map --html /tmp/zweigkarte.html
```
Das erzeugt die **fertige Seite** aus `karte_template.html` — Zweigbaum,
Legende, Zaehler und einen Datensatz pro Zweig, in WB-Farben und mit
Hell/Dunkel-Variante. Diese Datei dann unveraendert per `Artifact`
veroeffentlichen, `favicon: "🌿"`.

Die Seite nicht von Hand nachbauen und das Template nicht pro Aufruf
umschreiben — es ist die eine Stelle, an der das Aussehen der Karte
definiert ist. Aendert der Nutzer das Design, aendere `karte_template.html`,
nicht die erzeugte Datei.

Veroeffentlichst du eine Karte erneut, nimm denselben Dateipfad wie beim
letzten Mal, damit das Artifact an seiner URL aktualisiert wird statt ein
zweites anzulegen.

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
