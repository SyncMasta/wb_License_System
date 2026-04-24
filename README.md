# WB Odoo Workspace

Dieser Workspace enthält alle WB-Odoo-Module + Architektur-Dokumente
für Claude Code.

## Quickstart mit Claude Code in VS Code

### Einmalig einrichten

```bash
# 1. Node.js 18+ sicherstellen
node --version

# 2. Claude Code CLI installieren
npm install -g @anthropic-ai/claude-code

# 3. VS Code Extension
#    Öffne VS Code → Extensions (Ctrl+Shift+X)
#    Suche: "Claude Code" (vom Publisher "Anthropic")
#    → Install

# 4. Diesen Workspace in VS Code öffnen
code .

# 5. Claude Code öffnen:
#    Spark-Icon in der Activity Bar (links) anklicken
#    ODER Cmd+Esc (Mac) / Ctrl+Esc (Windows/Linux)
```

Beim ersten Öffnen wirst du nach deinem Anthropic-Account gefragt.
Pro-Abo reicht für Claude Code. Es lädt dann automatisch `CLAUDE.md`
als Kontext.

### Typischer Workflow

1. In VS Code öffnen → Claude Code sieht automatisch alle Dateien
2. Claude Code öffnen (Spark-Icon)
3. Einfach schreiben was du willst, z.B.:
   - *"Bau Sprint 1 aus der ARCHITECTURE.md"*
   - *"Fixe den Fehler im UStVA-XML-Export"*
   - *"Füge einen Telegram-Command `/leads` hinzu zu wb_odoo_automations"*
4. Claude Code macht Änderungen direkt in den Dateien mit Diff-Ansicht
5. Akzeptieren oder ablehnen pro Change

## Enthaltene Inhalte

### Root-Level
| Datei | Zweck |
|---|---|
| `CLAUDE.md` | Automatisch geladener Kontext für Claude Code |
| `DECISIONS.md` | Alle 47 fixierten Architektur-Entscheidungen |
| `ARCHITECTURE.md` | Komplette Architektur-Doku v1.4 (Nordstern) |
| `README.md` | Dieses Dokument |
| `.gitignore` | Python/Odoo-sinnvoll vorkonfiguriert |

### Modul-Briefings (`docs/modules/`)
| Datei | Modul | Status |
|---|---|---|
| `wb_odoo_automations.md` | Interne Automatisierungen | ✅ Produktiv (retrospektive Doku) |
| `wb_elster_reports.md` | ELSTER-Modul | 🟡 MVP-Skelett, Roadmap zu v1.0 |
| `wb_subscription.md` | Vertriebs-Backend | 🚧 Noch nicht gebaut (Briefing) |
| `wb_license_client.md` | Kunden-seitiges Modul | 🚧 Noch nicht gebaut (Briefing) |

### Guides (`docs/guides/`)
| Datei | Zweck |
|---|---|
| `deployment.md` | Deployment auf s02, Rollback, Multi-Stage |
| `security.md` | Threat-Model, DSGVO, Incident-Response |

### Code
| Ordner | Status |
|---|---|
| `wb_odoo_automations/` | Produktives Modul (läuft auf s02, v0.6) |
| `wb_elster_reports/` | MVP-Skelett (NICHT produktionsreif!) |

## Git-Setup (empfohlen)

```bash
git init
git add .
git commit -m "Initial workspace from Chat 2026-04-23"

# Remote setzen (z.B. GitHub)
git remote add origin git@github.com:SyncMasta/wb-odoo-modules.git
git push -u origin main
```

## Deploy auf Produktions-Server (s02)

Siehe Abschnitt "Workflow für Code-Änderungen" in `CLAUDE.md`.

Quick-Version:
```bash
# Modul als tar.gz packen
tar -czf /tmp/wb_odoo_automations.tar.gz wb_odoo_automations/

# Hochladen
scp /tmp/wb_odoo_automations.tar.gz root@s02:/tmp/

# Auf s02 entpacken + updaten
ssh root@s02 'bash -s' << 'EOF'
cd /opt/odoo19/custom_addons/
rm -rf wb_odoo_automations
tar -xzf /tmp/wb_odoo_automations.tar.gz
chown -R odoo19:odoo19 wb_odoo_automations/
find wb_odoo_automations -name __pycache__ -exec rm -rf {} +
systemctl stop odoo19
sudo -u odoo19 /opt/odoo19/venv/bin/python3.12 /opt/odoo19/odoo-bin \
  -c /etc/odoo19.conf -u wb_odoo_automations -d Main --stop-after-init
systemctl start odoo19
EOF
```

## Nächste Schritte

Laut `ARCHITECTURE.md` Kapitel 10 (Sprint-Plan):

1. **Sprint 1** — `wb_subscription` Core + Key/Code-Generator + bcrypt + UI (7-9h)
2. **Sprint 2** — `wb_license_client` + Ping-Endpoint (5-7h)
3. **Sprint 3** — Dokumente (Zertifikat, EULA, Rechnung) (6-8h)
4. ... siehe ARCHITECTURE.md

## Support-Kontakt (bei externen Prüfungen)

- Tobias Wissen
- WISSEN BERATUNG
- https://www.wissen-beratung.de
