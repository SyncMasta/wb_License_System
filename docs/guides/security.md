# Security-Guide

Sicherheits-Richtlinien für das WISSEN BERATUNG Odoo-Ökosystem.
Relevant für alle Module die mit Kunden-Daten, Lizenzen oder
Payment-Flows arbeiten.

---

## Threat Model

Bevor man Sicherheits-Entscheidungen trifft, muss man wissen
**wogegen** man sich schützt.

### Was wir schützen

| Asset | Wert | Schutzbedarf |
|---|---|---|
| **Lizenz-Datenbank (wb.license.key)** | Vertriebs-Existenz | Hoch |
| **Activation-Codes** | Finanzieller Verlust (Piracy) | Sehr hoch |
| **Fernet-Key (ENV/ir.config_parameter)** | Schlüssel zu allen Pending-Tickets | Sehr hoch |
| **Kunden-Adressen und USt-IDs** | DSGVO-Bußgeld, Reputation | Hoch |
| **Zahlungsdaten (account_payment)** | Finanzen, Vertrauen | Sehr hoch |
| **Telegram-Bot-Token** | Spam-Möglichkeit, Missbrauch | Mittel |
| **Stripe-API-Keys** | Direkter Finanzzugriff | Sehr hoch |

### Wogegen wir schützen

| Angreifer | Motivation | Realistisch? |
|---|---|---|
| **Script Kiddies** | Enumeration, Spam | Ja, passiv |
| **Piracy/Crack-Communities** | Kostenlose Lizenzen | Ja, bei Erfolg |
| **Mitbewerber** | Schaden, Rufmord | Theoretisch |
| **Ex-Kunden/Mitarbeiter** | Rache, gezielter Schaden | Möglich |
| **Staatliche Akteure** | unwahrscheinlich | Vernachlässigbar |
| **Email-Hijacker** | Activation-Code-Diebstahl | **Hauptrisiko!** |

### Was wir NICHT schützen (explizit)

- **Vollständiger Schutz gegen hochmotivierte Angreifer mit DB-Zugang.**
  Wenn jemand root-SSH-Zugriff hat, ist Game Over.
- **Schutz gegen geplante Social Engineering.**
  Wenn ein Kunde seinen Activation-Code weitergibt, können wir das nicht verhindern.
- **Schutz gegen Quantum-Attacks.** In 10 Jahren relevant, nicht jetzt.

---

## Multi-Layer-Defense für Lizenz-System

### Layer 1: Input-Validation

Alle Eingaben clientseitig UND serverseitig prüfen:

```python
# Im wb.key.generator

def validate_key_format(key: str) -> bool:
    """Format-Check OHNE DB-Zugriff. Cheap und sicher."""
    import re
    m = re.match(r'^WB-([A-Z]{4})-([a-f0-9]{8})([A-Z2-7]{2})$', key)
    if not m:
        return False
    # Checksum prüfen
    product_code, uuid_short, checksum = m.groups()
    return compute_checksum(f"WB-{product_code}-{uuid_short}") == checksum
```

Zweck: Brute-Force gegen die Datenbank abzuwehren. Wer Format-ungültige
Keys schickt, bekommt sofort 400 ohne DB-Query.

### Layer 2: Rate-Limiting

Jeder öffentliche Endpoint hat Limits:

| Endpoint | Limit | Begründung |
|---|---|---|
| `/api/license/activate` | 5/h/IP | Code-Brute-Force verhindern |
| `/api/license/check` | 100/h/IP | DoS verhindern, legitim bei vielen Instanzen |
| `/api/license/trial` | 1/24h/Email | Trial-Abuse verhindern |
| `/api/license/migrate` | 2/Tag/Key | Spam-Anträge verhindern |
| `/license/verify/<key>` | 20/h/IP | Enumeration abwehren |
| `/activate/<ticket>/verify-otp` | 5 Versuche pro Ticket | OTP-Brute-Force |

**Implementation:** Eigene `wb.rate.limit`-Table mit TTL-Einträgen,
stündlicher Cleanup-Cron.

```python
def _check_rate_limit(self, bucket_key, max_per_period, period_seconds):
    """Gibt True zurück wenn im Limit, False wenn überschritten.
    Erhöht Counter atomar."""
    # SQL-based, um Race-Conditions zu vermeiden
    ...
```

### Layer 3: Secret-Hashing

**bcrypt für Activation-Codes:**

```python
import bcrypt

def hash_activation_code(code: str) -> bytes:
    # 12 Runden = ~200ms auf typischer Hardware
    return bcrypt.hashpw(code.encode('utf-8'), bcrypt.gensalt(rounds=12))

def verify_activation_code(code: str, stored: bytes) -> bool:
    try:
        return bcrypt.checkpw(code.encode('utf-8'), stored)
    except ValueError:
        return False  # stored hash invalid
```

**Warum bcrypt und nicht SHA256?**
- bcrypt ist **absichtlich langsam** (200ms)
- Gegen Brute-Force: 5 Versuche/h × 24h = 120 Versuche/Tag
  → bei 10^37 möglichen Codes: Jahrtausende für Erfolg
- SHA256 wäre in 1ms berechenbar → 86.4 Mio Versuche/Tag möglich

### Layer 4: Channel-Separation + IP-Binding

**Das Activation-Code-Design:**

Der Code verlässt den Server **niemals** im Klartext per Email.
Auslieferung läuft über zwei voneinander unabhängige Kanäle:

1. **Kanal A (Email):** Ticket-Link `/activate/<ticket>` — ohne Auth-Info
2. **Kanal B (Portal):** Email-OTP an die hinterlegte Adresse

Nur wer beide Kanäle kontrolliert (= Email-Account gehackt UND
Ticket-Link abgefangen) kommt an den Code. Das Szenario ist in
~99% der Fälle ein und dasselbe (Mailbox-Hijack), aber:

- OTP ist nur 10min gültig → Zeitdruck für Angreifer
- Nach 5 OTP-Fehlversuchen wird Ticket invalidiert
- Code ist nach Anzeige nur 10min sichtbar
- Fehlversuche werden geloggt und können alerten

**IP-Binding (DECISION #48c, v1.5):**

Zusätzlich zur Channel-Separation wird das Ticket an die **erste
aufrufende IP** gepinnt. Szenario: Kunde leitet die Aktivierungs-
Email aus Versehen an Dritte weiter (Assistent, Support-Ticket-System,
öffentliche Mailing-Liste). Ohne IP-Binding könnte jemand, der parallel
Mailbox-Zugriff hat, den Flow übernehmen, **bevor** der Kunde selbst
den Link klickt.

```python
@http.route('/activate/<ticket_token>', type='http', auth='public')
def activate_landing(self, ticket_token, **kw):
    ticket = request.env['wb.activation.ticket'].sudo().search(
        [('name', '=', ticket_token)], limit=1)

    client_ip = request.httprequest.remote_addr

    if not ticket.bound_ip:
        # Erster Aufruf — IP festpinnen
        ticket.bound_ip = client_ip
    elif ticket.bound_ip != client_ip:
        # Späterer Aufruf von anderer IP — loggen, ggf. blockieren
        ticket._log_ip_mismatch(client_ip)
        if ticket.ip_mismatch_count >= 3:
            ticket.state = 'revoked'
            return request.render('wb_subscription.ticket_revoked_template')

    # ... normales Portal-Rendering
```

**Bewusste Schwäche:** Dynamische IPs (Mobilnetz-Wechsel Home-Office →
Smartphone) führen zu False-Positives. Deshalb: Erste 3 IP-Mismatches
werden nur **gezählt und geloggt**, nicht sofort hart blockiert.
Erst ab 3 Mismatches wird das Ticket revoked. Compromise zwischen
Usability und Security.

**Was NICHT geblockt wird:**
- User wechselt vom Smartphone (Mobilnetz-IP) zum WLAN-Gerät
  → zählt als 1 Mismatch, geht durch
- User benutzt VPN und toggelt es
  → zählt als Mismatch, nach 3 blockiert (akzeptabel)

**Was geblockt wird:**
- Angreifer mit Mailbox-Zugriff aus anderem Netzwerk als der echte User
  → spätestens beim 3. Zugriff-Versuch geblockt, alle bis dahin sichtbar
  in `wb.license.event` mit IP, User-Agent, Timestamp

### Layer 5: Encryption-at-Rest

**Activation-Code-Verschlüsselung für Portal-Anzeige:**

Problem: Der Code muss im Portal angezeigt werden können. Er ist aber
nur als bcrypt-Hash in der DB. bcrypt ist **einweg**, also kann er
nicht zurück-entschlüsselt werden.

**Lösung:** Fernet-Encryption beim Ticket-Create (fixiert durch DECISION #48).

```python
import os
from cryptography.fernet import Fernet

def _get_fernet_key(env) -> bytes:
    """Lädt Fernet-Key mit ENV-Präferenz, Fallback auf ir.config_parameter.

    ENV-Variable ist bevorzugt, weil sie NICHT in DB-Backups enthalten ist.
    Das schützt gegen "DB-Leak kompromittiert alle Tickets".
    """
    key = os.environ.get('WB_SUBSCRIPTION_FERNET_KEY')
    if key:
        return key.encode('utf-8')
    key = env['ir.config_parameter'].sudo().get_param(
        'wb_subscription.fernet_key')
    if not key:
        raise UserError(_(
            "Fernet-Key nicht konfiguriert. Bitte ENV-Variable "
            "WB_SUBSCRIPTION_FERNET_KEY setzen oder ir.config_parameter "
            "'wb_subscription.fernet_key' pflegen."))
    return key.encode('utf-8')

def encrypt_code_for_ticket(env, code: str) -> bytes:
    f = Fernet(_get_fernet_key(env))
    return f.encrypt(code.encode('utf-8'))

def decrypt_code_from_ticket(env, ciphertext: bytes) -> str:
    f = Fernet(_get_fernet_key(env))
    return f.decrypt(ciphertext).decode('utf-8')
```

**Wichtig für Fernet-Key:**
- **Niemals in Git.**
- **Default beim Install:** Modul generiert Key automatisch und speichert ihn in
  `ir.config_parameter` `wb_subscription.fernet_key` (via `post_init_hook`).
  Das ist **Plug-and-play**, aber der Key landet im DB-Backup.
- **Empfohlen für Produktion:** ENV-Variable `WB_SUBSCRIPTION_FERNET_KEY` —
  nicht in DB-Backup, nicht in DB-Dump. Wenn gesetzt, wird sie bevorzugt;
  `ir.config_parameter` wird dann ignoriert.
- **Migration nach ENV:** siehe README.rst Abschnitt "High-Security-Option"
- **Access-Control:** Der Parameter in `ir.config_parameter` ist über die
  Gruppe `group_wb_subscription_manager` abgesichert
- Generierung: `Fernet.generate_key()` (URL-safe base64 von 32 Bytes)
- Rotation: alle 12 Monate oder nach vermutetem Leak
- **Nach erfolgreicher Activation:** `ticket.encrypted_code = False` setzen
  (siehe DECISION #48b) — minimiert die Menge an aktiven Ciphertexts

**Was bei Fernet-Key-Leak passiert:**
- Alle noch nicht consumed Tickets sind kompromittiert
- Alle Pending-Activations müssen manuell überprüft werden
- Neuer Fernet-Key generieren, alten invalidieren
- Alle Tickets mit `state != 'consumed'` revoken + Kunden neuen Ticket-Link senden

### Layer 6: Audit-Logging

**Jeder relevante Event in `wb.license.event`:**

- Activation-Versuche (erfolgreich und fehlgeschlagen)
- OTP-Versuche
- Code-Reveal im Portal
- Pings (für Analytics)
- Migration-Anträge
- State-Changes

**Nicht geloggt werden:**
- Klartext-Codes (niemals!)
- Passwörter
- Stripe-Secrets

### Layer 7: Incident-Response

Bei vermutetem Security-Incident:

1. **Sofort:** Betroffenen Account/Key `state='revoked'` setzen
2. **Binnen 1h:** Fernet-Key rotieren (wenn Ticket-Flow betroffen)
3. **Binnen 24h:** Admin-Passwörter + API-Keys rotieren
4. **Binnen 72h:** Ggf. DSGVO-Meldung an Datenschutzbehörde wenn
   Personendaten betroffen

---

## Kritische Do's und Don'ts

### ✅ DO

- **Alle Secrets in `ir.config_parameter`** mit Permission-Control
- **HTTPS zwingend** für alle Endpoints
- **Rate-Limiting auf jedem öffentlichen Endpoint**
- **Input-Validation client-side UND server-side**
- **bcrypt für Code-Hashes** (Runden: 12)
- **Fernet für temporäre Encryption** (Ticket-Codes)
- **Audit-Log jedes kritischen Events**
- **Regelmäßige DB-Backups** (verschlüsselt!)
- **SSH nur mit Key, nie mit Password**
- **Secrets rotieren** bei Verdacht
- **Logging mit Log-Level differenzieren** (INFO für Normales, WARN für Verdächtiges, ERROR für Fehler)

### ❌ DON'T

- **Secrets im Code oder Git** — niemals, auch nicht als Default!
- **Passwords oder Codes im Klartext loggen** — `_logger` ist oft öffentlich
- **Activation-Codes per Email senden** — Channel-Separation!
- **HTTPS-Zertifikate über Let's Encrypt ohne Monitoring** — auto-renew kann scheitern
- **Permissions via `sudo()` großzügig** — granular bleiben
- **User-Input direkt in SQL-Queries** — SQL-Injection!
- **Alte Security-Library-Versionen** — regelmäßig `pip list --outdated`
- **Admin-Login mit schwachem Passwort** — Password-Manager + 2FA

---

## Logging-Regeln

### Was geloggt werden DARF

- **INFO:** Normale Operations (User-Logins, Cron-Starts)
- **WARNING:** Unerwartete aber nicht kritische Situationen
- **ERROR:** Exceptions, Fehlschläge

### Was NIEMALS geloggt werden darf

```python
# ❌ FALSCH — Code im Log
_logger.info(f"Activating with code {code}")

# ❌ FALSCH — Passwort im Log
_logger.debug(f"User password: {password}")

# ❌ FALSCH — Raw Ticket-Content
_logger.info(f"Ticket: {ticket.encrypted_code}")

# ❌ FALSCH — Stripe-Secret
_logger.error(f"Stripe call failed: key={STRIPE_KEY}")


# ✅ RICHTIG — nur Metadaten
_logger.info(f"Activation attempt for license {license.id} from IP {ip}")
_logger.info(f"User {user.login} logged in")
_logger.warning(f"Failed activation: wrong_code for license {license.id}")
```

### Log-Rotation

```bash
# /etc/logrotate.d/odoo19
/var/log/odoo/odoo19.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
    maxsize 100M
}
```

---

## Spezifische Maßnahmen pro Modul

### wb_subscription

- **Fernet-Key in ir.config_parameter**, Read-Permission nur für Manager
- **Rate-Limiting auf allen API-Endpoints** (Tabelle siehe oben)
- **reCAPTCHA v3 auf Public-Pages** (Verify + Trial-Form)
- **Ticket-TTL** streng einhalten (72h max.)
- **OTP-Fehlversuche zählen** und Ticket invalidieren bei 5
- **Code-Anzeigezeit** auf 10 Min begrenzen (JavaScript + Server-Check)
- **Cookies HttpOnly + Secure + SameSite=Strict** für Portal-Sessions

### wb_license_client

- **Key-Storage in ir.config_parameter** (nicht in Model-Feld)
- **HTTP-Calls nur mit Timeout** (10s für Check, 15s für Activate)
- **TLS-Verify aktiv lassen** (`requests` default, nicht deaktivieren)
- **DB-UUID prüfen vor Send** (nicht default leer)

### wb_odoo_automations

- **Telegram-Bot-Token in ir.config_parameter**, nicht in Code
- **Chat-IDs pro User** speichern, nicht global-default

### wb_elster_reports *(historisch — Produkt wurde 2026-04-24 verworfen)*

- USt-IDs DSGVO-konform behandeln (Zugriffskontrolle)
- XML-Dateien verschlüsselt speichern (ir.attachment mit mimetype)
- Keine automatischen Uploads an elster.de (zu riskant ohne Zert-Auth)
- Log-Level bei XML-Generation INFO (keine Inhalte!)

---

## DSGVO-Konformität

### Personenbezogene Daten im System

| Daten | Modul | Rechtsgrundlage | Aufbewahrungsfrist |
|---|---|---|---|
| Kunden-Adressen | res.partner | Art. 6.1.b (Vertrag) | Vertragsende + 10 Jahre (§ 147 AO) |
| E-Mail-Adressen | res.partner, wb.license.trial.request | Art. 6.1.b / 6.1.f | Trial: 1 Jahr nach Expire |
| USt-IDs | res.partner | Art. 6.1.c (Gesetzl. Pflicht) | Vertragsende + 10 Jahre |
| IP-Adressen | wb.license.event | Art. 6.1.f (Berechtigtes Interesse) | 90 Tage |
| Lead-Data | crm.lead, wb.license.trial.request | Art. 6.1.a (Einwilligung) | 2 Jahre nach letztem Kontakt |

### Pflichten

- **Auskunftsrecht:** Button in wb_subscription Admin: "DSGVO-Auskunft für Kunde X" → Export aller Daten
- **Löschrecht:** Aktionen "Löschen gem. Art. 17 DSGVO" die alle personenbezogenen Felder auf "ANONYMIZED" setzen
- **Datenschutz-Folgeabschätzung** (DSFA) für wb_subscription erforderlich (automatisierte Entscheidung über Vertragsdaten)

### Was du selbst machen musst

1. **Auftragsverarbeitungsvertrag (AVV)** mit allen Drittanbietern:
   - serverdiscounter.com (Hoster)
   - Stripe (Payment)
   - Brevo (Email-Versand)
   - Telnyx (SMS, falls verwendet)

2. **Datenschutzerklärung** auf wissen-beratung.de:
   - Alle oben genannten Verarbeitungen aufgelistet
   - Speicherdauer transparent gemacht
   - Betroffenenrechte erklärt

3. **Verzeichnis von Verarbeitungstätigkeiten** führen (Art. 30 DSGVO)
   - Für jede Verarbeitung: Zweck, Daten, Rechtsgrundlage, Speicherdauer

4. **Datenschutz-Beauftragter** nicht zwingend (unter 20 MA), aber empfohlen

---

## Penetration-Test-Checklist

Wenn du vor dem Release einen Pen-Test (selbst oder extern) machst:

- [ ] **API-Endpoints:** Rate-Limiting greift?
- [ ] **SQL-Injection:** Alle Inputs durch ORM (keine raw SQL)?
- [ ] **XSS:** Alle HTML-Outputs escapeed oder `Markup()`?
- [ ] **CSRF:** Alle State-Changing Endpoints mit Token (oder API-Key)?
- [ ] **Authentication:** Keine Endpoints ohne auth wenn sensible Daten
- [ ] **Brute-Force:** Login-Attempts limitiert?
- [ ] **TLS:** Alle HTTPS, kein Mixed-Content?
- [ ] **Security Headers:** HSTS, X-Frame-Options, CSP?
- [ ] **File-Upload:** Content-Type-Check, Größen-Limit?
- [ ] **Dependency-Scan:** `pip-audit` oder `safety check` clean?

Tools die dabei helfen:
- **sqlmap** für SQL-Injection-Tests
- **OWASP ZAP** für automatisierte Scans
- **Burp Suite Community** für manuelle Tests
- **dependabot** (GitHub) für Dep-Audits
- **Snyk** für Container-Scans (falls Docker)

---

## Secret-Rotation-Schedule

| Secret | Rotation | Wie? |
|---|---|---|
| **Fernet-Key** (wb_subscription) | 12 Monate | Neuen Key generieren, alten behalten für Decryption von Pending-Tickets, nach 72h alten löschen |
| **Stripe-Keys** | Bei Personal-Wechsel / Verdacht | In Stripe-Dashboard rotieren, in Odoo updaten |
| **Telegram-Bot-Token** | Bei Verdacht | BotFather neuer Token, in Odoo updaten |
| **reCAPTCHA-Secret** | Alle 12 Monate | In Google Console regenerieren |
| **Odoo-Admin-Passwort** | 6 Monate | In res.users im UI ändern |
| **SSH-Keys (root@s02)** | 12 Monate | Neuer Key lokal, `ssh-copy-id` zum Server, alten entfernen |
| **Postgres-Passwort** | 12 Monate | `ALTER USER odoo19 WITH PASSWORD ...` + odoo.conf updaten |

---

## Incident-Response-Plan

Bei Sicherheits-Vorfall:

### Phase 1: Containment (< 1 Stunde)

1. Betroffene Komponente isolieren
   - Modul deaktivieren (`installable=False`)
   - Endpoint deaktivieren (nginx)
   - Key revoken (`state='revoked'`)

2. Sofort-Benachrichtigung
   - Telegram-Alert an Tobias
   - Email an betroffene Kunden (wenn >10 Min Downtime)

### Phase 2: Investigation (< 24 Stunden)

1. Logs sichern (vor Rotation!)
2. Umfang bestimmen: Wer ist betroffen?
3. Angriffspfad identifizieren
4. Andere kompromittierbare Systeme prüfen

### Phase 3: Eradication (< 72 Stunden)

1. Schwachstelle fixen (Code + Deploy)
2. Alle Secrets rotieren die potenziell exposed waren
3. Betroffene Kunden neue Lizenz-Daten geben

### Phase 4: Recovery

1. Services schrittweise wieder aktivieren
2. Monitoring verschärft für 2 Wochen
3. Betroffene Kunden kontaktieren mit Erklärung

### Phase 5: Post-Mortem

1. Detaillierter Bericht: Was war, was ist jetzt, was wird geändert
2. DSGVO-Meldung wenn Personendaten betroffen (binnen 72h!)
3. Tests + Monitoring erweitern damit's nicht wieder passiert

---

## Kontakte / Ressourcen

### Intern

- **Haupt-Admin:** Tobias Wissen (einziger aktuell)
- **Backup-Kontakt:** *(noch zu bestimmen)*

### Extern

- **Serverdiscounter Support:** für Hardware/Netz-Probleme
- **Datenschutzbehörde Niedersachsen** (für DSGVO-Incident-Meldungen): https://lfd.niedersachsen.de
- **BSI** (Cert-Bund): https://www.bsi.bund.de für Security-Advisories
- **Anwalt für IT-Recht:** *(vor Launch zu beauftragen)*

### Dokumentation

- **OWASP Top 10:** https://owasp.org/Top10/
- **Odoo Security Guide:** https://www.odoo.com/documentation/19.0/administration/maintain/security.html
- **DSGVO-Text:** https://dsgvo-gesetz.de/
- **BSI IT-Grundschutz:** https://www.bsi.bund.de/DE/Themen/Unternehmen-und-Organisationen/Standards-und-Zertifizierung/IT-Grundschutz/itgrundschutz_node.html
