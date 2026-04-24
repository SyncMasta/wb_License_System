# Claude Code — Projekt-Kontext

Diese Datei wird von Claude Code automatisch geladen. Sie enthält alles was
du brauchst um produktiv an den WB-Modulen zu arbeiten.

---

## Wer ist der Nutzer?

- **Name:** Tobias Wissen (Inhaber WISSEN BERATUNG)
- **Firma:** WISSEN BERATUNG — IT-Consulting & Automatisierung für KMU im DACH-Raum
- **Sprache:** Deutsch bevorzugt, technische Begriffe Englisch OK
- **Stil:** Direkt, pragmatisch, anti-fluff. Keine Marketing-Sprache.
- **Prinzipien:**
  - Odoo-Standards nutzen, keine Parallel-Welten bauen
  - Keine überflüssigen Abstraktionen (z.B. nicht n8n dazwischenschalten wo direkter Call reicht)
  - Updatefest & Git-tauglich (nichts über Odoo Studio)
  - Jedes Modul muss Standalone installierbar sein

---

## Projekt-Struktur

Dieser Workspace enthält **zwei Haupt-Module** plus Dokumentation:

```
wb_odoo_automations/     Interne Automatisierungen (produktiv, läuft auf s02)
wb_elster_reports/       Erstes verkäufliches Produkt (MVP-Skelett)
```

**In Zukunft:** `wb_subscription` (Vertriebs-Backend) und `wb_license_client`
(kommt als Dependency mit Produkt-Modulen). Architektur dafür siehe
`ARCHITECTURE.md`.

---

## Der Stack

### Produktions-Server
- **s02** (serverdiscounter.com, IP 185.216.214.34) — Odoo 19 Enterprise
- Pfade: `/opt/odoo19/` (venv, odoo-bin), `/etc/odoo19.conf` (Config)
- Service: `systemctl {start,stop,restart,status} odoo19`
- Custom Addons: `/opt/odoo19/custom_addons/`
- DB: `Main`, Python 3.12 im venv `/opt/odoo19/venv/`
- Logs: `/var/log/odoo/odoo19.log`

### Weitere Komponenten
- **s01** (Hetzner, IP 185.216.214.13) — Coolify für WB-CRM, Chatwoot etc.
- **Brevo** — Internes CRM/Marketing-Tool
- **n8n** — Workflow-Automatisierung: `beratung.n8n.wissen-beratung.de`
- **Zabbix** — Monitoring: `zabbix.wissen-beratung.de`
- **Telegram** — Push-Notifications

### Tech-Stack für Odoo-Module
- **Odoo 19** (Enterprise, SKR03-Kontenplan, l10n_de)
- **Python 3.12**
- **Postgres**
- **QWeb** für Reports
- **OWL** für Frontend-Komponenten (falls nötig)
- **bcrypt** für Password/Code-Hashing
- **requests** für HTTP-Calls

---

## Odoo 19 spezifische Besonderheiten

**Breaking Changes vs. Odoo 17/18 die schon aufgefallen sind:**

1. **res.groups Feld für User:**
   - Odoo ≤17: `group.users`
   - Odoo 18/19: `group.user_ids`
   - **Robust nutzen:** `sudo().search([('group_ids', 'in', group.id), ...])` statt `group.users`

2. **message_notify() akzeptiert weniger Parameter:**
   - ❌ `record_name`, `email_layout_xmlid` → wirft Error
   - ✅ Besser: `message_post()` mit `partner_ids` — ist stabiler und macht dasselbe

3. **Discuss-Benachrichtigungen:**
   - Wenn `author_id` nicht OdooBot ist und der Autor = Empfänger, wird Nachricht gefiltert
   - Robust: `author_id=env.ref('base.partner_root').id`

4. **HTML im message_post body:**
   - Muss `Markup` aus `markupsafe` sein, sonst wird alles escaped

5. **XPath in View-Inherits:**
   - Block-IDs ändern sich zwischen Versionen
   - Stabiler Anker: `<app name="crm">`, nicht `<block id='lead_generation_setting_container'>`

---

## Coding Conventions für WB-Module

### Datei-Struktur

```
wb_<modulname>/
├── __manifest__.py             author='WISSEN BERATUNG (Tobias Wissen)'
├── __init__.py                 from . import models
├── README.rst                  bei verkäuflichen Produkten
├── models/
│   ├── __init__.py
│   └── *.py                    _name = 'wb.<model>.<submodel>'
├── views/
│   └── *.xml                   IDs: wb_<modul>_<model>_<typ>
├── data/                       SQL-Sequenzen, Mail-Templates
├── security/
│   ├── wb_<modul>_security.xml  Gruppen
│   └── ir.model.access.csv
├── static/description/
│   ├── icon.png                128x128, Navy-Hintergrund #1D3C6E
│   └── index.html              bei verkäuflichen Produkten
└── doc/                        Nutzer-Doku
```

### Naming

- Modul-Präfix: `wb_` (niemals `wissen_beratung_` — zu lang)
- Models: `wb.<domain>.<thing>` z.B. `wb.elster.ustva`, `wb.telegram.notifier`
- Felder: `wb_` Präfix auf Odoo-Standard-Models (z.B. `res.partner.wb_license_ids`)
- XML-IDs: `wb_<modul>_<object>_<action>` z.B. `wb_elster_ustva_action_list`

### Code-Style

- Python 3.12-Syntax nutzen (f-strings, type hints)
- Dict-Literale mit `dict(key=value)` nur wenn's Code-Kenntnis erhöht
- Comments auf Deutsch OK für domain-spezifische Logik (Steuer, Buchhaltung)
- Docstrings auf Englisch für Klassen/Methoden
- _logger-Prefix: `[wb_<modul>]` z.B. `_logger.info("[wb_elster] Report generated")`

### Fehlerbehandlung

```python
# Immer try/except um Nicht-kritische Operations, um DB-Transaktion zu schützen
try:
    send_telegram(...)
except Exception as e:
    _logger.exception("[wb_xxx] Telegram failed: %s", e)
# Kritische Operations: kein try/except, Exception soll propagieren
```

### message_post / message_notify

```python
from markupsafe import Markup

# IMMER Markup für HTML body
body = Markup('<p>🎯 <strong>%s</strong></p>') % name

# message_post für Chatter + Notification
self.message_post(
    body=body,
    subject=subject,
    partner_ids=partner_ids,
    message_type='notification',
    subtype_xmlid='mail.mt_comment',
    author_id=env.ref('base.partner_root').id,  # OdooBot, nie self.env.user
)
```

---

## Workflow für Code-Änderungen

1. **Lokal ändern** im Workspace
2. **Validierung:**
   ```bash
   python3 -m py_compile <file.py>
   python3 -c "import xml.etree.ElementTree as ET; ET.parse('<file.xml>')"
   ```
3. **tar.gz bauen** und per scp auf s02:
   ```bash
   tar -czf /tmp/wb_<modul>.tar.gz wb_<modul>/
   scp /tmp/wb_<modul>.tar.gz root@s02:/tmp/
   ```
4. **Auf s02 deployen:**
   ```bash
   cd /opt/odoo19/custom_addons/
   rm -rf wb_<modul> && tar -xzf /tmp/wb_<modul>.tar.gz
   chown -R odoo19:odoo19 wb_<modul>/
   find wb_<modul> -name __pycache__ -exec rm -rf {} +

   systemctl stop odoo19
   sudo -u odoo19 /opt/odoo19/venv/bin/python3.12 /opt/odoo19/odoo-bin \
     -c /etc/odoo19.conf -u wb_<modul> -d Main --stop-after-init
   systemctl start odoo19
   ```
5. **Log prüfen:**
   ```bash
   tail -f /var/log/odoo/odoo19.log | grep -E "(wb_<modul>|ERROR)"
   ```

---

## Branding-Assets

- **Farben:** Navy `#1D3C6E`, Blue `#1A8BC4`, Cyan `#00C8E8`
- **Logo:** Auf allen offiziellen Dokumenten (Lizenzzertifikate, etc.)
- **Tonalität (Marke):** Pain-before-solution, keine hohlen Phrasen
- **Tonalität (Tobias persönlich):** Norddeutsch-pragmatisch

Detailliert in den Skills `wb-brand-voice` und `wb-personal-voice` dokumentiert.

---

## Was noch zu tun ist

Siehe `DECISIONS.md` und `ARCHITECTURE.md` für den Gesamt-Plan.

**Nächster Sprint laut Architektur:** `wb_subscription` Core (Sprint 1 aus
dem Sprint-Plan in `ARCHITECTURE.md` Kapitel 10).

---

## Wichtige Hinweise an Claude Code

Wenn du als Claude Code an diesem Projekt arbeitest:

1. **Lies zuerst `ARCHITECTURE.md`** bevor du strukturelle Entscheidungen triffst
2. **Lies `DECISIONS.md`** bevor du Entscheidungen re-evaluierst
3. **Nutze Odoo-Standards** wo möglich (product.product, sale.subscription,
   mail.template, account_followup)
4. **Keine Nacharbeit an Sprint 0 (Architektur-Design)** — die Entscheidungen
   sind fixiert, bitte nur ausführen, nicht umplanen außer auf expliziten Wunsch
5. **Sicherheits-kritische Stellen:** Activation-Code NIE loggen, NIE per Email,
   NIE auf Zert/Rechnung
6. **Deployments immer** mit Service-Stop vorher (sonst "Address already in use")
