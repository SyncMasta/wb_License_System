# Deployment-Guide

Einheitlicher Prozess zum Deployen aller WB-Odoo-Module auf den
Produktions-Server **s02**.

---

## Server-Übersicht

| Eigenschaft | Wert |
|---|---|
| **Hostname** | s02 (serverdiscounter.com) |
| **IP** | 185.216.214.34 |
| **OS** | Debian/Ubuntu (prüfen mit `cat /etc/os-release`) |
| **SSH-User** | root |
| **Odoo-Version** | 19.0 Enterprise |
| **Odoo-Service** | `odoo19` (systemd) |
| **Odoo-User** | `odoo19` (Unix-User) |
| **Odoo-Pfad** | `/opt/odoo19/` |
| **Venv** | `/opt/odoo19/venv/` |
| **Python** | 3.12 (im venv) |
| **Config** | `/etc/odoo19.conf` |
| **Custom Addons** | `/opt/odoo19/custom_addons/` |
| **Logs** | `/var/log/odoo/odoo19.log` |
| **DB** | `Main` |
| **Postgres** | lokal, User `odoo19` |

---

## Pre-Deployment Checkliste

Bevor du ein Modul deployed:

- [ ] Alle Python-Dateien syntaktisch korrekt (`python3 -m py_compile`)
- [ ] Alle XML-Dateien wohlgeformt (`xmllint --noout`)
- [ ] `__manifest__.py` version hochgezählt (!)
- [ ] `__pycache__/` überall entfernt
- [ ] Geänderte Security-Files im manifest data-Liste
- [ ] Dependency-Tree im manifest vollständig
- [ ] Bei produktiv genutzten Modulen: Testumgebung vorhanden oder Backup erstellt

---

## Der Standard-Deployment-Flow

### Schritt 1: Lokal packen

```bash
# Im Workspace-Root
cd ~/repos/wb-subscription-workspace

# pycache bereinigen
find wb_odoo_automations -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null

# Validierung
python3 -m py_compile wb_odoo_automations/models/*.py

for f in wb_odoo_automations/views/*.xml wb_odoo_automations/data/*.xml; do
  xmllint --noout "$f" && echo "OK: $f" || echo "FAIL: $f"
done

# Packen
tar -czf /tmp/wb_odoo_automations.tar.gz wb_odoo_automations/

# Größe checken (sollte <1MB sein)
ls -lh /tmp/wb_odoo_automations.tar.gz
```

### Schritt 2: Hochladen

```bash
scp /tmp/wb_odoo_automations.tar.gz root@185.216.214.34:/tmp/
```

### Schritt 3: Auf dem Server deployen

```bash
ssh root@185.216.214.34 'bash -s' << 'EOF'
set -e  # Bei Fehler abbrechen

MODULE="wb_odoo_automations"
ADDONS="/opt/odoo19/custom_addons"

echo "=== Backup des aktuellen Moduls ==="
if [ -d "$ADDONS/$MODULE" ]; then
  tar -czf "/root/backups/${MODULE}_$(date +%Y%m%d_%H%M%S).tar.gz" -C "$ADDONS" "$MODULE"
fi

echo "=== Altes Modul entfernen ==="
rm -rf "$ADDONS/$MODULE"

echo "=== Neues Modul entpacken ==="
cd "$ADDONS"
tar -xzf "/tmp/${MODULE}.tar.gz"

echo "=== Rechte setzen ==="
chown -R odoo19:odoo19 "$MODULE/"

echo "=== pycache entfernen (doppelt hält besser) ==="
find "$MODULE" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

echo "=== Odoo stoppen ==="
systemctl stop odoo19
sleep 2

echo "=== Modul upgraden ==="
sudo -u odoo19 /opt/odoo19/venv/bin/python3.12 /opt/odoo19/odoo-bin \
  -c /etc/odoo19.conf \
  -u "$MODULE" \
  -d Main \
  --stop-after-init \
  --log-level=info

echo "=== Odoo starten ==="
systemctl start odoo19
sleep 3

echo "=== Status checken ==="
systemctl status odoo19 --no-pager | head -10

echo "=== Log-Tail (letzte 10 Zeilen) ==="
tail -10 /var/log/odoo/odoo19.log

echo "=== Deployment fertig ==="
EOF
```

### Schritt 4: Post-Deployment-Verifikation

```bash
# Log live beobachten
ssh root@185.216.214.34 'tail -f /var/log/odoo/odoo19.log | grep -E "(wb_|ERROR|WARNING)"'

# Im Browser checken
# https://<odoo-url>/odoo/action-base.action_module_configuration_form
# → Modul-Version stimmt mit manifest überein
# → Keine roten Error-Hinweise
```

---

## All-in-one One-Liner

Für schnelles Re-Deployment während Entwicklung:

```bash
# Von lokalem Rechner, im Workspace-Root
MODULE="wb_odoo_automations"
find $MODULE -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
tar -czf /tmp/${MODULE}.tar.gz $MODULE/ && \
scp /tmp/${MODULE}.tar.gz root@185.216.214.34:/tmp/ && \
ssh root@185.216.214.34 "
  cd /opt/odoo19/custom_addons/ && \
  rm -rf $MODULE && \
  tar -xzf /tmp/${MODULE}.tar.gz && \
  chown -R odoo19:odoo19 $MODULE/ && \
  find $MODULE -name __pycache__ -exec rm -rf {} + ; \
  systemctl stop odoo19 && \
  sudo -u odoo19 /opt/odoo19/venv/bin/python3.12 /opt/odoo19/odoo-bin \
    -c /etc/odoo19.conf -u $MODULE -d Main --stop-after-init && \
  systemctl start odoo19 && \
  tail -20 /var/log/odoo/odoo19.log
"
```

---

## Bekannte Gotchas und Fixes

### "Address already in use" (Port 8069)

**Ursache:** Odoo beim Update-Befehl nicht korrekt gestoppt.

**Fix:**
```bash
systemctl stop odoo19
sleep 3
pkill -f odoo-bin  # nur wenn systemctl stop nicht reicht
```

### Modul wird nicht gefunden

**Ursache:** Falsche Permissions oder `__init__.py` fehlt.

**Check:**
```bash
ls -la /opt/odoo19/custom_addons/$MODULE/
# Sollte gehören: odoo19:odoo19
# __init__.py muss existieren
```

### XML-Parse-Error beim Update

**Ursache:** Tippfehler im XML, oder View-ID kollidiert.

**Fix:**
```bash
# Vorher LOKAL validieren:
for f in $MODULE/views/*.xml; do xmllint --noout "$f"; done

# Auf Server nachsehen welche Zeile:
grep -E "ParseError|xml" /var/log/odoo/odoo19.log | tail -5
```

### `column not found` nach Feld-Rename

**Ursache:** Odoo hat alte Spalte in DB, neue Feld-Definition im Code.

**Fix:** Modul upgraden (`-u`), nicht nur installieren (`-i`):
```bash
sudo -u odoo19 /opt/odoo19/venv/bin/python3.12 /opt/odoo19/odoo-bin \
  -c /etc/odoo19.conf -u $MODULE -d Main --stop-after-init
```

Wenn das nicht hilft: Assets-Cache leeren:
```bash
sudo -u odoo19 /opt/odoo19/venv/bin/python3.12 /opt/odoo19/odoo-bin \
  -c /etc/odoo19.conf -u $MODULE -d Main --dev=all --stop-after-init
```

### Cron-Job läuft nicht

**Check:**
```bash
# In Odoo als Admin:
# Settings → Technical → Automation → Scheduled Actions
# → Modul-Cron finden → "nextcall" prüfen
# → "Aktiv" prüfen

# Oder via SQL:
psql -U odoo19 Main -c "SELECT name, nextcall, active FROM ir_cron WHERE name LIKE 'WB%';"
```

### Python-Dependency fehlt

**Ursache:** `external_dependencies` im Manifest werden NICHT auto-installiert.

**Fix:**
```bash
sudo -u odoo19 /opt/odoo19/venv/bin/pip install requests bcrypt cryptography
```

Alle WB-Module haben diese Dependencies — einmal installieren reicht.

---

## Backup-Strategie

### Tägliche automatische Backups

Du solltest auf s02 einen Cron-Job haben:

```bash
# In /etc/cron.d/odoo-backup:
0 3 * * * root /opt/scripts/backup-odoo.sh
```

mit Inhalt:

```bash
#!/bin/bash
BACKUP_DIR=/root/backups/odoo
DATE=$(date +%Y%m%d_%H%M%S)
mkdir -p "$BACKUP_DIR"

# DB-Backup
sudo -u odoo19 pg_dump Main | gzip > "$BACKUP_DIR/main_${DATE}.sql.gz"

# Custom-Addons-Backup
tar -czf "$BACKUP_DIR/custom_addons_${DATE}.tar.gz" -C /opt/odoo19 custom_addons

# Alte Backups löschen (älter als 30 Tage)
find "$BACKUP_DIR" -name "*.gz" -mtime +30 -delete
```

### Vor jedem Deployment

Das Deployment-Script oben macht **Modul-Backup** vor dem Überschreiben.

### DB-Backup vor kritischen Updates

Wenn ein Modul-Update Schema-Änderungen hat (neue Pflichtfelder, Selection-Änderungen):

```bash
ssh root@185.216.214.34 "sudo -u odoo19 pg_dump Main | gzip > /root/backups/manual_pre_deploy_$(date +%Y%m%d_%H%M%S).sql.gz"
```

Dauert ~30 Sekunden, gibt dir Rollback-Option.

---

## Rollback

Wenn ein Deployment schief geht:

### Modul zurück auf Vor-Version

```bash
ssh root@185.216.214.34 << 'EOF'
MODULE="wb_odoo_automations"
# Neuestes Backup finden
LATEST=$(ls -t /root/backups/${MODULE}_*.tar.gz | head -1)
echo "Restoring from: $LATEST"

systemctl stop odoo19
cd /opt/odoo19/custom_addons/
rm -rf $MODULE
tar -xzf "$LATEST"
chown -R odoo19:odoo19 $MODULE/

sudo -u odoo19 /opt/odoo19/venv/bin/python3.12 /opt/odoo19/odoo-bin \
  -c /etc/odoo19.conf -u $MODULE -d Main --stop-after-init
systemctl start odoo19
EOF
```

### Komplette DB zurückspielen

**⚠️ Achtung: ALLE Änderungen seit Backup gehen verloren!**

```bash
ssh root@185.216.214.34 << 'EOF'
# Dump-File wählen
DUMP="/root/backups/manual_pre_deploy_XXX.sql.gz"

systemctl stop odoo19
sudo -u postgres psql -c "DROP DATABASE Main;"
sudo -u postgres psql -c "CREATE DATABASE Main OWNER odoo19;"
zcat "$DUMP" | sudo -u odoo19 psql Main
systemctl start odoo19
EOF
```

---

## Monitoring nach Deployment

### Was unmittelbar prüfen

1. **Odoo-Service läuft:**
   ```bash
   systemctl status odoo19
   ```

2. **Keine Error-Spikes in den letzten 60s:**
   ```bash
   tail -100 /var/log/odoo/odoo19.log | grep ERROR
   ```

3. **Login funktioniert:** Browser öffnen, einloggen, Hauptmenü sollte laden

4. **Modul-spezifische Checks:**
   - wb_odoo_automations: Cron-Liste angucken, alle "aktiv"?
   - wb_elster_reports: Menü "ELSTER" da?
   - wb_subscription: Menü "WB Lizenzen" da?

### Was über nächste 24h monitoren

- **Zabbix:** Dashboard prüfen, keine neuen Warnings?
- **Telegram:** Kommen die Morning Briefings pünktlich? Wenn nicht:
  Cron hat Fehler — sofort im Log nachgucken.

---

## Multi-Stage-Setup (zukünftig empfohlen)

Aktuell hast du nur **s02 Produktion**. Für Sicherheit ist eine
Staging-Instanz stark empfohlen.

### Aufbau

```
Entwicklung (lokal)
    ↓ Push
Staging (s02, DB: "Staging")
    ↓ Test erfolgreich
Produktion (s02, DB: "Main")
```

### Staging-DB anlegen

```bash
ssh root@185.216.214.34 << 'EOF'
# Staging als Clone von Main
sudo -u postgres psql -c "CREATE DATABASE Staging TEMPLATE Main;"

# Odoo kann mit mehreren DBs, nur --database beim Start
# Zweiter Odoo-Prozess auf anderem Port:
# z.B. /opt/odoo19-staging.conf mit http_port = 8070
EOF
```

Dann deployed man erst auf Staging:
```bash
# Mit Staging-DB testen
sudo -u odoo19 /opt/odoo19/venv/bin/python3.12 /opt/odoo19/odoo-bin \
  -c /etc/odoo19.conf -u $MODULE -d Staging --stop-after-init
```

Wenn OK: dann auf Produktion (gleiche Command, `-d Main`).

**Aufwand für Staging-Setup:** ~2h einmalig.

---

## Troubleshooting-Cheatsheet

| Symptom | Wahrscheinliche Ursache | Fix |
|---|---|---|
| Odoo startet nicht | Syntax-Error im Modul | Log checken, Modul deaktivieren |
| "No module named X" | External dep fehlt | pip install im venv |
| "View does not exist" | XML-Reference zeigt auf gelöschten Record | Migration-Script oder --dev=all |
| Performance-Drop nach Update | Assets-Cache | `--dev=all` einmalig |
| Cron läuft, aber Nix passiert | Method-Name tippfehler oder Record inaktiv | ir_cron-Tabelle prüfen |
| "Access Denied" obwohl Admin | Record Rules fehlen | ACL + record_rules checken |
| Datenbank langsam | Fehlende Indexes auf neuen Feldern | `_sql_constraints` oder index=True |

---

## Sicherheits-Hinweise

### Secrets

**NIEMALS in Git checkin:**
- Telegram-Bot-Token
- Stripe-API-Keys
- ELSTER-Zertifikate
- Fernet-Keys
- Odoo-Admin-Passwörter
- SSH-Keys

Alle Secrets gehören in `ir.config_parameter` (via Odoo-UI eingetragen)
oder in ENV-Variablen via `systemd`-Service-File.

### SSH-Zugang

- **Nur per SSH-Key**, kein Password-Login
- `PermitRootLogin prohibit-password` in `/etc/ssh/sshd_config`
- 2FA erwägen (z.B. google-authenticator-pam)
- Fail2Ban für Brute-Force-Schutz

### HTTPS

- **Alle Endpoints** müssen HTTPS sein
- Let's Encrypt mit Certbot, auto-renewal
- HSTS-Header in nginx setzen
- Besonders: `/api/license/*` Endpoints dürfen NIE über HTTP
  (Activation-Code würde im Klartext übers Netz gehen!)

---

## Kontakt bei Problemen

- **Tobias Wissen** (Inhaber / einziger Admin bisher)
- **Server-Anbieter** (serverdiscounter.com Support für Hardware/Netz)
- **Anthropic / Claude Code** für Code-Debugging

Für Notfälle: aktuelles DB-Backup + Modul-Backup immer aktuell halten.
Mit einem Full-Backup kann man s02 binnen 30 Minuten auf einem
neuen Server wieder hochziehen.

---

## Erst-Installation der Lizenz-Plattform (wb_subscription + wb_license_client)

Schritt-für-Schritt für s02 nach Sprint 1–8.

### 1. Python-Dependencies installieren (einmalig)

```bash
ssh root@s02
sudo -u odoo19 /opt/odoo19/venv/bin/pip install bcrypt cryptography
```

### 2. Beide Module hochladen

Vom Workspace aus:

```bash
cd "Odoo Addons/wb_License_System"
tar -czf /tmp/wb_subscription.tar.gz wb_subscription/
tar -czf /tmp/wb_license_client.tar.gz wb_license_client/
scp /tmp/wb_subscription.tar.gz /tmp/wb_license_client.tar.gz root@s02:/tmp/
```

### 3. Auf s02 entpacken + installieren

```bash
ssh root@s02
cd /opt/odoo19/custom_addons/
tar -xzf /tmp/wb_subscription.tar.gz
tar -xzf /tmp/wb_license_client.tar.gz
chown -R odoo19:odoo19 wb_subscription/ wb_license_client/
find wb_subscription wb_license_client -name __pycache__ -exec rm -rf {} +

systemctl stop odoo19
sudo -u odoo19 /opt/odoo19/venv/bin/python3.12 /opt/odoo19/odoo-bin \
  -c /etc/odoo19.conf -i wb_subscription,wb_license_client \
  -d Main --stop-after-init
systemctl start odoo19
```

Beim ersten Start generiert `wb_subscription` automatisch einen Fernet-Key
und legt ihn unter `ir.config_parameter` `wb_subscription.fernet_key` ab.

### 4. System-Parameter konfigurieren

In Odoo: **Settings → Technical → Parameters → System Parameters**.

| Parameter | Wert | Pflicht |
|---|---|---|
| `wb_subscription.fernet_key` | (auto-generiert beim Install) | ✅ |
| `wb_subscription.recaptcha_site_key` | von google.com/recaptcha (v3) | für Public-Verify |
| `wb_subscription.recaptcha_secret_key` | von google.com/recaptcha (v3) | für Public-Verify |
| `wb_subscription.webshop_api_key` | langer Random-String, in Webseite einbauen | für Order-Endpoint |
| `wb_subscription.telegram_token` | aus BotFather | für Tobias-Alerts |
| `wb_subscription.telegram_chat_id` | Tobias' Chat-ID | für Tobias-Alerts |

Optional Rate-Limit-Overrides:

| Parameter | Default | Format |
|---|---|---|
| `wb_subscription.rate_limit_check` | 100/3600 | `<max>/<seconds>` |
| `wb_subscription.rate_limit_activate` | 5/3600 | wie oben |
| `wb_subscription.rate_limit_trial` | 1/86400 | wie oben |

### 5. Fernet-Key in ENV verschieben (empfohlen)

Default landet der Fernet-Key in der DB — ENV-Variante ist besser
geschützt vor DB-Leak.

```bash
# 1. Aktuellen Key auslesen (in Odoo: Settings → System Parameters)
#    Den Wert von wb_subscription.fernet_key kopieren

# 2. systemd-Service erweitern
sudo systemctl edit odoo19
# Füge ein:
# [Service]
# Environment="WB_SUBSCRIPTION_FERNET_KEY=<kopierter_key>"

sudo systemctl daemon-reload
sudo systemctl restart odoo19

# 3. In Odoo: System Parameter wb_subscription.fernet_key löschen
#    (ENV wird ab jetzt bevorzugt geladen)
```

### 6. Erstes Lizenz-Produkt anlegen

In Odoo: **WB Lizenzen → Lizenz-Produkte → Erstellen**

| Feld | Beispiel |
|---|---|
| Name | Telnyx-Integration |
| Verkaufspreis | 199.00 € |
| Tab "WB Lizenz" → Ist Lizenz-Produkt | ✓ |
| Technischer Code | `TELE` |
| Odoo-Modul-Name | `wb_telnyx_voip` |
| Erlaubte Instanzen | 1 |
| Trial-Tage | 7 |
| Activation-Frist | 90 |

### 7. Smoke-Test

1. **Lizenz manuell anlegen:**
   WB Lizenzen → Lizenzen → Neu mit `state=issued`,
   beliebigem Partner, Lizenz-Produkt aus Schritt 6.
   System generiert Public Key automatisch.

2. **Activation-Code generieren:**
   Auf der Lizenz-Form: Field `activation_hash` ist gefüllt.
   Über Python-Shell den Code generieren (für Test):
   ```python
   gen = env['wb.key.generator']
   code = gen.generate_activation_code()
   license = env['wb.license.key'].browse(LICENSE_ID)
   license.activation_hash = gen.hash_activation_code(code)
   ticket = env['wb.activation.ticket'].create({
       'license_id': license.id,
       'email': license.partner_id.email,
       'encrypted_code': gen.encrypt_code(code),
   })
   print('Code:', code, 'Ticket:', ticket.name)
   ```

3. **Portal-Flow durchspielen:**
   `https://wissen-beratung.de/activate/<ticket>` aufrufen,
   OTP anfordern, OTP eingeben, Code wird angezeigt.

4. **Public-Verify testen:**
   `https://wissen-beratung.de/license/verify/<key>` —
   sollte mit reCAPTCHA-Challenge laden, dann Lizenz-Daten zeigen.

5. **Tests laufen lassen:**
   ```bash
   sudo -u odoo19 /opt/odoo19/venv/bin/python3.12 /opt/odoo19/odoo-bin \
     -c /etc/odoo19.conf -d Main \
     --test-tags wb_subscription --stop-after-init
   ```

### 8. Cron-Status prüfen

In Odoo: **Settings → Technical → Scheduled Actions** —
es sollten 9 WB-Crons aktiv sein:

- WB Subscription: License State Update
- WB Subscription: Activation Reminders
- WB Subscription: Renewal Reminders
- WB Subscription: Trial Reminders
- WB Subscription: Ticket Cleanup
- WB Subscription: Rate-Limit Cleanup
- WB Subscription: Daily Telegram-Summary
- WB Subscription: Dezember-Renewal-Invoices
- WB License Client: Daily Ping

### 9. Dashboard öffnen

WB Lizenzen → Dashboard — sollte 0 Lizenzen, 0,00 € MRR zeigen.
