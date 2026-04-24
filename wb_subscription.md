# wb_subscription — Modul-Briefing

**Status:** 🚧 Not yet built — Design-Doku als Grundlage für Implementation
**Zielversion:** `19.0.1.0.0`
**Läuft auf:** Deine eigene Odoo-Instanz (Vertriebs-Backend) — **nicht** beim Kunden
**Dependencies:** `base`, `mail`, `product`, `sale_subscription`, `account`, `account_followup`, `portal`

---

## Zweck

Zentrales Vertriebs-Backend für WISSEN BERATUNG Produkt-Module. Verwaltet:

- **Produkte** (über `product.product` mit WB-Erweiterungen)
- **Kunden** (über `res.partner`)
- **Abos** (über `sale.subscription` mit WB-Erweiterungen)
- **Lizenzschlüssel** (eigenes Modell `wb.license.key`)
- **Aktivierungs-Tickets** (eigenes Modell `wb.activation.ticket`)
- **Migrations-Anträge** (eigenes Modell `wb.license.migration.request`)
- **Trial-Anfragen** (eigenes Modell `wb.license.trial.request`)
- **Benachrichtigungs-Log** (eigenes Modell `wb.notification.log`)
- **HTTP-Endpoints** für Kunden-Clients (Ping, Activate, Trial, Migrate, Verify)

Das Modul ist **produkt-agnostisch** — jedes künftige WB-Produkt (ELSTER, DATEV, DSGVO, …) kann sich ohne Code-Änderung einhängen.

---

## Module-Struktur

```
wb_subscription/
├── __manifest__.py
├── __init__.py
├── README.rst
├── models/
│   ├── __init__.py
│   ├── product_product.py           # Erweiterungen auf product.product
│   ├── sale_subscription.py         # Erweiterungen auf sale.subscription
│   ├── res_partner.py               # Erweiterungen auf res.partner
│   ├── wb_license_key.py            # Hauptmodell
│   ├── wb_license_event.py          # Audit-Log
│   ├── wb_license_tag.py            # Tagging
│   ├── wb_activation_ticket.py      # Portal-Download-Tickets
│   ├── wb_license_migration.py      # Umzugs-Anträge
│   ├── wb_license_trial.py          # Trial-Requests + Lead-Capture
│   ├── wb_notification_log.py       # Benachrichtigungs-Log
│   └── wb_key_generator.py          # AbstractModel: Key/Code/Ticket-Algorithmen
├── controllers/
│   ├── __init__.py
│   ├── api_license.py               # /api/license/check, /activate, /migrate
│   ├── api_trial.py                 # /api/license/trial
│   ├── portal_activation.py         # /activate/<ticket>, Email-OTP
│   ├── public_verify.py             # /license/verify/<key> (öffentlich)
│   └── api_order.py                 # /api/wb_subscription/order (Webshop)
├── data/
│   ├── ir_sequence_data.xml         # Sequenzen (LIZ-YYYY-NNNNN etc.)
│   ├── wb_license_tag_data.xml      # VIP, Reseller, OEM, Test-Kunde
│   ├── mail_template_data.xml       # alle Mail-Templates
│   └── ir_cron_data.xml             # Cron-Jobs (Reminder, Renewal, etc.)
├── reports/
│   ├── license_certificate_report.xml
│   ├── license_certificate_template.xml  # QWeb-Template
│   ├── license_eula_report.xml
│   ├── license_eula_template.xml
│   └── account_move_template_ext.xml     # Rechnungs-Erweiterung
├── views/
│   ├── wb_license_key_views.xml
│   ├── wb_license_trial_views.xml
│   ├── wb_license_migration_views.xml
│   ├── wb_notification_log_views.xml
│   ├── product_product_views.xml
│   ├── sale_subscription_views.xml
│   ├── res_partner_views.xml
│   ├── portal_templates.xml         # Ticket-Download-UI
│   ├── public_verify_templates.xml  # Öffentliche Verify-Page
│   └── menu.xml
├── security/
│   ├── wb_subscription_security.xml
│   └── ir.model.access.csv
├── static/
│   ├── description/
│   │   ├── icon.png
│   │   └── index.html
│   └── src/
│       └── scss/                    # Branding für PDF-Reports
└── tests/
    ├── __init__.py
    ├── test_key_generator.py
    ├── test_license_activation.py
    ├── test_ticket_workflow.py
    └── test_trial_workflow.py
```

---

## Modelle im Detail

### 1. `product.product` — Erweiterungen

Neue Felder (alle mit `wb_` Präfix):

| Feld | Typ | Default | Beschreibung |
|---|---|---|---|
| `wb_is_license_product` | Boolean | False | Kennzeichnet Lizenz-Produkt |
| `wb_technical_code` | Char(4) | - | 'ELST', 'DATV', ... für Key-Format |
| `wb_module_technical_name` | Char | - | 'wb_elster_reports' (Odoo-Modul) |
| `wb_instance_limit` | Integer | 1 | Erlaubte Instanzen pro Lizenz |
| `wb_extra_instance_price` | Float | - | Preis pro Zusatzinstanz |
| `wb_trial_days` | Integer | 7 | Trial-Laufzeit |
| `wb_eula_template_id` | M2O mail.template | - | EULA-Vorlage |
| `wb_certificate_template_id` | M2O ir.actions.report | - | Zertifikat-Template |
| `wb_activation_grace_days` | Integer | 90 | Tage in denen Code aktivierbar bleibt |

**Constraints:**
- `wb_technical_code` muss unique sein wenn gesetzt
- `wb_technical_code` muss exakt 4 Großbuchstaben sein (Regex: `^[A-Z]{4}$`)
- Wenn `wb_is_license_product=True`, dann `wb_technical_code` required

### 2. `sale.subscription` — Erweiterungen

| Feld | Typ | Beschreibung |
|---|---|---|
| `wb_license_key_ids` | O2M → wb.license.key | Alle Keys zu diesem Abo |
| `wb_is_license_sub` | Boolean computed | True wenn ≥1 Line mit `wb_is_license_product` |
| `wb_license_count` | Integer computed | Anzahl Keys |

**Hook-Methode:**
```python
def _wb_issue_license_keys(self):
    """Wird beim Payment-In aufgerufen. Erzeugt pro Lizenz-Line einen Key."""
    # iteriert über sale_order lines → wb_is_license_product
    # erzeugt wb.license.key mit state='issued'
    # erzeugt wb.activation.ticket
    # versendet wb_payment_received + wb_activation_instructions
```

### 3. `res.partner` — Erweiterungen

| Feld | Typ | Beschreibung |
|---|---|---|
| `wb_license_key_ids` | O2M → wb.license.key | Alle Keys des Kunden |
| `wb_license_count` | Integer computed | Für Smart-Button |
| `wb_active_license_count` | Integer computed | Nur state='active' |

**Smart-Button auf Partner-Form:** "N Lizenzen" — öffnet gefilterte Liste.

### 4. `wb.license.key` — Hauptmodell

Das zentrale Modell. Alle Details siehe ARCHITECTURE.md Kapitel 5.4.

**Felder:**

| Feld | Typ | Required | Besonderheit |
|---|---|---|---|
| `name` | Char | Yes | = Public Key, readonly nach create |
| `display_name` | Char computed | - | z.B. "ELSTER für Müller GmbH" |
| `product_id` | M2O product.product | Yes | domain: `[('wb_is_license_product','=',True)]` |
| `partner_id` | M2O res.partner | Yes | |
| `subscription_id` | M2O sale.subscription | No | NULL bei Trials |
| `state` | Selection | Yes | siehe State-Machine unten |
| `activation_hash` | Binary | No | bcrypt-Hash, leer nach Activation ("burned") |
| `activation_hash_method` | Char | - | default 'bcrypt', future-proof |
| `activated_at` | Datetime | - | Zeitpunkt der Erstaktivierung |
| `activated_fingerprint` | Char | - | SHA256(domain+db_uuid+timestamp) |
| `activation_expires_at` | Datetime | - | Code nur bis dahin aktivierbar |
| `valid_from` | Date | Yes | |
| `valid_to` | Date | Yes | |
| `grace_until` | Date computed | - | Abgeleitet aus account_followup |
| `last_renewal_date` | Date | - | |
| `bound_domain` | Char | - | Gesetzt nach Activation |
| `bound_db_uuid` | Char | - | |
| `instance_limit` | Integer | - | kopiert aus product beim Create |
| `instance_current` | Integer computed | - | aus events gezählt |
| `last_seen_at` | Datetime | - | Letzter Ping |
| `last_seen_ip` | Char | - | |
| `last_seen_user_agent` | Char | - | |
| `ping_count_total` | Integer | - | für Diagnostik |
| `event_ids` | O2M wb.license.event | - | inverse='license_id' |
| `migration_ids` | O2M wb.license.migration.request | - | |
| `certificate_ids` | O2M ir.attachment | - | computed aus res_model+res_id |
| `tag_ids` | M2M wb.license.tag | - | |
| `note` | Text | - | Kunden-sichtbar (falls Portal) |
| `internal_note` | Text | - | nur Tobias |
| `company_id` | M2O res.company | Yes | |
| `currency_id` | related company_id.currency_id | - | für monetary fields |

**State-Machine:**

```
issued ───[activate() successful]──► active
                                       │
                                       ├──[payment_in renewal]──► active
                                       │
                                       ├──[followup_status=with_overdue]──► grace
                                       │                                       │
                                       │                                       └──[payment_in]──► active
                                       │                                       │
                                       │                                       └──[grace_until passed]──► expired
                                       │
                                       ├──[manual revoke]──► revoked
                                       │
                                       └──[customer cancelled]──► cancelled

trial ───[7d expire]──► expired
trial ───[convert to paid]──► (neuer key in state='issued')
```

**Methoden:**

```python
@api.model_create_multi
def create(self, vals_list):
    """Generiert Key bei Create, wenn nicht explizit gesetzt."""

def action_activate(self, activation_code, domain, db_uuid, ip=None):
    """Aktiviert den Key mit Code-Prüfung. Setzt state='active'."""

def action_revoke(self, reason):
    """Manuell sperren. state='revoked'."""

def action_reactivate(self):
    """Von revoked/expired zurück auf active."""

def action_renew(self, new_valid_to):
    """Verlängert Key. Bei Bedarf: neues Zertifikat generieren."""

def action_open_migration_wizard(self):
    """Öffnet Wizard zum Migrations-Antrag (von Admin-Seite)."""

def action_generate_certificate_pdf(self):
    """Erzeugt PDF-Zertifikat und hängt als attachment an."""

def _compute_grace_status(self):
    """Leitet state=grace ab aus partner.followup_status."""

def _notify_state_change(self, old_state, new_state):
    """Triggert passendes Mail-Template bei State-Änderung."""
```

**Constraints:**
- `name` unique
- `state != 'issued'` erfordert `activated_at` gesetzt
- `instance_limit >= 1`

### 5. `wb.license.event` — Audit-Log

**Jeder relevante State-Change / Ping / Fehlversuch erzeugt einen Event.**

| Feld | Typ | Besonderheit |
|---|---|---|
| `license_id` | M2O wb.license.key | required, ondelete='cascade' |
| `event_type` | Selection | siehe Liste unten |
| `timestamp` | Datetime | default=now |
| `ip_address` | Char | bei HTTP-Events gesetzt |
| `user_agent` | Char | |
| `domain` | Char | aus Ping-Request |
| `db_uuid` | Char | aus Ping-Request |
| `details` | Text | JSON-kodiert, flexibel |
| `created_by_id` | M2O res.users | NULL bei API-Calls |

**Event-Types:**
- `key_generated` — Key + Code erzeugt
- `activation` — Erstaktivierung erfolgreich
- `activation_failed` — Falscher Code (IP+UA geloggt)
- `ticket_viewed` — Ticket-Link wurde geöffnet
- `otp_sent` — Email-OTP versendet
- `otp_verified` — OTP korrekt eingegeben
- `otp_failed` — OTP falsch
- `code_revealed` — Code wurde im Portal angezeigt
- `ping` — Täglicher Ping vom Client
- `renewal` — Jahres-Verlängerung
- `trial_started`, `trial_converted`, `trial_expired`
- `grace_started`, `grace_ended`
- `migration_requested`, `migration_approved`, `migration_rejected`
- `revoked`, `reactivated`
- `certificate_generated`

**Views:**
- List-View filterbar nach `event_type`, `timestamp`, `license_id`
- Form-View read-only (audit log ist immutable!)

### 6. `wb.license.tag` — Kategorisierung

| Feld | Typ |
|---|---|
| `name` | Char required unique |
| `color` | Integer (0-11, Odoo-Standard) |
| `description` | Text |
| `license_ids` | M2M inverse |

**Default-Tags (in `wb_license_tag_data.xml`):**
- VIP (Farbe 2)
- Reseller (Farbe 3)
- OEM (Farbe 4)
- Test-Kunde (Farbe 5)

### 7. `wb.activation.ticket` — Portal-Download-Tickets

| Feld | Typ | Besonderheit |
|---|---|---|
| `name` | Char | Format: `TCKT-{base64url(uuid4).rstrip('=')}` (22 Zeichen) |
| `license_id` | M2O wb.license.key | required, ondelete='cascade' |
| `state` | Selection | 'pending' → 'otp_sent' → 'code_revealed' → 'consumed' oder 'expired' |
| `email` | Char | Email wo OTP hingeht (aus partner_id) |
| `current_otp_hash` | Binary | bcrypt-Hash der aktuell gültigen OTP |
| `otp_sent_at` | Datetime | |
| `otp_valid_until` | Datetime | otp_sent_at + 10 Minuten |
| `otp_attempts` | Integer | Wird bei jedem Fehlversuch erhöht |
| `otp_max_attempts` | Integer | default=5 |
| `code_revealed_at` | Datetime | Zeitpunkt als Code angezeigt wurde |
| `code_hidden_at` | Datetime | code_revealed_at + 10 Minuten |
| `created_at` | Datetime | default=now |
| `expires_at` | Datetime | created_at + 72 Stunden |
| `ip_address` | Char | IP beim Create |
| `last_accessed_ip` | Char | IP beim letzten Aufruf |

**Methoden:**

```python
def action_send_otp(self):
    """Generiert 6-stelligen OTP, hasht ihn, versendet per Email."""

def action_verify_otp(self, otp_input):
    """Prüft OTP. Bei Erfolg: state='code_revealed', gibt Code zurück (unhashed, temporär)."""
    # WICHTIG: Code wird NICHT aus DB gelesen (existiert dort nicht!)
    # Trick: Der Activation-Code wird *beim Key-Create* RAM-hold
    # und dann beim Ticket-Create ebenfalls als *second hash* gespeichert
    # HIER: Nach OTP-Erfolg dekryptieren wir... moment.
    # ALTERNATIVE: Code bei Ticket-Create als encrypted blob mit
    # Fernet (symmetric) speichern, Key aus Server-Config.
    # TODO: entscheiden in Sprint 1 — siehe "Design-Entscheidung #1" unten

def _cron_cleanup_expired_tickets(self):
    """Cron täglich: alte Tickets/OTPs invalidieren."""
```

**Design-Entscheidung #1 — wie kommt der Code beim Verify an die Anzeige?**

Problem: Der Activation-Code steht nur als bcrypt-Hash in `wb.license.key`.
Nach OTP-Verifikation müssen wir ihn aber im Portal *anzeigen* können.

**Option A: Fernet-Encrypted Blob im Ticket**
- Beim Ticket-Create: `ticket.encrypted_code = fernet.encrypt(code)`
- Fernet-Key liegt in `ir.config_parameter` (oder ENV-Variable)
- Nach OTP-Verify: `code = fernet.decrypt(ticket.encrypted_code)`
- Vorteil: Klar und umsetzbar
- Nachteil: Ein zentraler Server-Key entschlüsselt alles (bei DB+Server-Leak kompromittiert)

**Option B: Code-Teil im Ticket-Token einbetten**
- Ticket-Token = `TCKT-{random_bytes}` wobei random_bytes zusammen mit OTP den Code rekonstruieren
- Komplex, unübersichtlich

**Option C: Code zu Ticket-Create-Zeit in Server-Session halten**
- Geht nicht, weil Ticket 72h gültig ist und Odoo-Prozesse restarten

**→ Empfehlung:** **Option A** mit Fernet-Verschlüsselung.
- Server-Key `wb_subscription.fernet_key` in `ir.config_parameter` (niemals in Git!)
- Separate Permission für Lesen dieses Parameters (nur `group_wb_subscription_manager`)
- Bei Ticket-Consume (nach erfolgreicher OTP): `encrypted_code` wird gelöscht
- Dokumentation: "Bei vermutetem Leak: Alle Pending-Tickets revoken + Fernet-Key rotieren"

### 8. `wb.license.migration.request`

(siehe ARCHITECTURE.md Kapitel 5.6 — unverändert)

**Zusatzfelder:**
- `state` Selection: 'pending' → 'approved' / 'rejected' → 'completed'
- `new_fingerprint` Char computed

**Workflow:**
1. Kunde postet an `/api/license/migrate`
2. Request mit state='pending' angelegt, Telegram + Email an Tobias
3. Tobias öffnet → prüft → Approve oder Reject
4. Bei Approve: Key-Record wird auf neue Domain/DB-UUID umgeschrieben, Event 'migration_approved' geloggt, neues Zertifikat generiert, Email an Kunde
5. Bei Reject: Email an Kunde mit Begründung

### 9. `wb.license.trial.request`

(siehe ARCHITECTURE.md Kapitel 5.7)

**Zusatzfelder für Lead-Qualifizierung:**
- `company_size` Selection: 'solo', '2-10', '11-50', '51-250', '251+'
- `industry` Char
- `expected_use_case` Text

Diese Felder sind **optional** aber bei Ausfüllen-Raten >50% extrem wertvoll für Lead-Scoring.

### 10. `wb.notification.log`

(siehe ARCHITECTURE.md Kapitel 5.8 — unverändert)

### 11. `wb.key.generator` — AbstractModel

Zentrale Helfer-Klasse für alle Key-/Code-/Ticket-Algorithmen.

```python
class WbKeyGenerator(models.AbstractModel):
    _name = 'wb.key.generator'
    _description = 'WB Key/Code/Ticket Generator'

    @api.model
    def generate_public_key(self, product_code): ...

    @api.model
    def generate_activation_code(self): ...

    @api.model
    def hash_activation_code(self, code): ...

    @api.model
    def verify_activation_code(self, code, stored_hash): ...

    @api.model
    def validate_key_format(self, key): ...

    @api.model
    def validate_activation_code_format(self, code): ...

    @api.model
    def generate_ticket_token(self): ...

    @api.model
    def generate_email_otp(self): ...

    @api.model
    def compute_fingerprint(self, domain, db_uuid): ...

    @api.model
    def encrypt_code(self, plaintext): ...     # Fernet

    @api.model
    def decrypt_code(self, ciphertext): ...    # Fernet
```

Alle Algorithmen siehe ARCHITECTURE.md Kapitel 4.

**Getestet in:** `tests/test_key_generator.py`

---

## HTTP-Endpoints (Controllers)

### Controller-Struktur

```python
# controllers/api_license.py

class ApiLicenseController(http.Controller):

    @http.route('/api/license/check',
                type='json', auth='public', methods=['POST'],
                csrf=False, cors='*')
    def check_license(self, **kwargs):
        ...

    @http.route('/api/license/activate',
                type='json', auth='public', methods=['POST'],
                csrf=False, cors='*')
    def activate_license(self, **kwargs):
        ...

    @http.route('/api/license/migrate',
                type='json', auth='public', methods=['POST'],
                csrf=False, cors='*')
    def migrate_license(self, **kwargs):
        ...
```

**Wichtig:**
- Alle API-Endpoints `type='json'` (kein HTML)
- `auth='public'` (kein Login nötig — Auth via Key+Code)
- `csrf=False` (API-Calls, kein Form)
- `cors='*'` für Pings von beliebigen Domains

**Rate-Limiting:**
Über `ir.config_parameter`:
- `wb_subscription.rate_limit_activate` — z.B. `5/1h/ip` (5 Calls pro h pro IP)
- `wb_subscription.rate_limit_trial` — z.B. `1/24h/email` (1 Trial pro E-Mail in 24h)
- `wb_subscription.rate_limit_verify` — z.B. `10/1h/ip`

Implementierung via ir-Table mit TTL-Einträgen + `ir.cron` für Cleanup.

### Alle Endpoints

| Endpoint | Method | Auth | CORS | Zweck |
|---|---|---|---|---|
| `/api/license/check` | POST JSON | public | * | Täglicher Ping |
| `/api/license/activate` | POST JSON | public | * | Erstaktivierung |
| `/api/license/migrate` | POST JSON | public | * | Umzugs-Antrag |
| `/api/license/trial` | POST JSON | public | *.wissen-beratung.de | Lead-Capture |
| `/api/wb_subscription/order` | POST JSON | public | *.wissen-beratung.de | Webshop-Bestellung |
| `/activate/<ticket>` | GET HTML | public | - | Ticket-Landing-Page |
| `/activate/<ticket>/send-otp` | POST JSON | public | - | OTP anfordern |
| `/activate/<ticket>/verify-otp` | POST JSON | public | - | OTP verifizieren + Code anzeigen |
| `/license/verify/<key>` | GET HTML | public | - | Öffentliche Verify-Page (mit reCAPTCHA) |

### Detaillierte Request/Response-Specs

Siehe ARCHITECTURE.md Kapitel 6.

---

## PDF-Reports

### Lizenzzertifikat

**Datei:** `reports/license_certificate_template.xml`

**QWeb-Aufbau:**

```xml
<template id="license_certificate_document">
    <t t-call="web.html_container">
        <t t-foreach="docs" t-as="o">
            <t t-call="web.external_layout">
                <div class="page">
                    <!-- Komplett weißer Hintergrund -->
                    <div class="wb_cert_container">
                        <!-- WB-Logo groß, zentriert oben -->
                        <div class="wb_cert_logo">
                            <img src="/wb_subscription/static/src/img/logo.png"/>
                        </div>

                        <!-- Titel -->
                        <h1>LIZENZBESTÄTIGUNG / LICENSE</h1>

                        <!-- Meta: Zertifikat-Nr, Datum -->
                        <div class="wb_cert_meta">
                            <span>Zertifikat-Nr: <t t-esc="o.certificate_number"/></span>
                            <span>Ausstelldatum: <t t-esc="o.issue_date"/></span>
                        </div>

                        <!-- Lizenznehmer -->
                        <section class="wb_cert_section">
                            <h2>Lizenznehmer</h2>
                            <p t-field="o.partner_id.name"/>
                            <p t-field="o.partner_id.street"/>
                            <p><t t-esc="o.partner_id.zip"/> <t t-esc="o.partner_id.city"/></p>
                            <p t-if="o.partner_id.vat">USt-ID: <t t-esc="o.partner_id.vat"/></p>
                        </section>

                        <!-- Lizenz-Info -->
                        <section class="wb_cert_section">
                            <h2>Lizenz</h2>
                            <dl>
                                <dt>Produkt:</dt>
                                <dd t-esc="o.product_id.name"/>
                                <dt>Lizenztyp:</dt>
                                <dd t-esc="dict(o.fields_get('state')['state']['selection']).get(o.state)"/>
                                <dt>Laufzeit:</dt>
                                <dd><t t-esc="o.valid_from"/> – <t t-esc="o.valid_to"/></dd>
                                <dt>Instanzen:</dt>
                                <dd><t t-esc="o.instance_limit"/></dd>
                                <dt t-if="o.bound_domain">Gebunden an:</dt>
                                <dd t-if="o.bound_domain" t-esc="o.bound_domain"/>
                            </dl>
                        </section>

                        <!-- Key-Box (mit Cyan-Rahmen) -->
                        <div class="wb_cert_key_box">
                            <p>Lizenzschlüssel:</p>
                            <code t-esc="o.name"/>
                        </div>

                        <!-- QR-Code mit Verify-URL -->
                        <div class="wb_cert_qr">
                            <img t-att-src="'/report/barcode/?type=QR&amp;value=' + o._get_verify_url() + '&amp;width=150&amp;height=150'"/>
                        </div>

                        <!-- Footer -->
                        <div class="wb_cert_footer">
                            <p>Online-Verifikation: <t t-esc="o._get_verify_url()"/></p>
                            <p>Der Aktivierungs-Code wurde Ihnen separat zugestellt.</p>
                        </div>
                    </div>
                </div>
            </t>
        </t>
    </t>
</template>
```

**CSS in `static/src/scss/license_certificate.scss`:**
- Navy `#1D3C6E` nur für Überschriften (h1, h2)
- Cyan `#00C8E8` nur für Key-Box-Rahmen (2px solid)
- Logo-Größe: max 200px breit
- Fließtext: schwarz, serif (z.B. Georgia) für Urkunden-Look
- Meta-Angaben: 10pt grau, rechts oben

### EULA-Template

**Datei:** `reports/license_eula_template.xml`

**Struktur:** Siehe ARCHITECTURE.md Kapitel 7.2.

**Wichtig:** EULA-Text als **Jinja-Template** im `mail.template` speichern, nicht als hardcoded XML — so kann Tobias (oder Anwalt) den Text ohne Code-Änderung anpassen.

### Rechnungs-Erweiterung

**Datei:** `reports/account_move_template_ext.xml`

XPath-Inherit des Standard-Rechnungs-Templates. Fügt pro Lizenz-Line ein:
- "Lizenzschlüssel: XYZ"
- "Gebunden an: Domain"
- Leistungszeitraum (= valid_from bis valid_to)

Footer: "Aktualisierte Lizenzbestätigung folgt nach Zahlungseingang."

---

## Mail-Templates

Alle in `data/mail_template_data.xml`. Liste + Zweck:

| XML-ID | Zweck | Anhang |
|---|---|---|
| `mail_template_trial_activated` | Trial-Key per Mail | Public Key inline |
| `mail_template_trial_ending_soon` | Tag 5 Reminder | - |
| `mail_template_trial_expired` | Tag 7 Expired | Kauf-CTA |
| `mail_template_payment_received` | Erstkauf-Bestätigung | Zertifikat-PDF + EULA-PDF |
| `mail_template_activation_instructions` | Ticket-Link für Code | - (NUR Link!) |
| `mail_template_activation_reminder_7d` | 7 Tage nach Issue | - |
| `mail_template_activation_reminder_30d` | 30 Tage nach Issue | - |
| `mail_template_activation_otp` | 6-stelliger OTP | - |
| `mail_template_renewal_reminder_60d` | 60 Tage vor Ablauf | - |
| `mail_template_renewal_reminder_30d` | 30 Tage vor Ablauf | - |
| `mail_template_renewal_invoice_sent` | Dezember-Rechnung | Rechnung-PDF |
| `mail_template_grace_started` | Letzte Mahnstufe erreicht | - |
| `mail_template_grace_last_day` | 1 Tag vor Expired | - |
| `mail_template_license_expired` | Expired | - |
| `mail_template_migration_requested` | Bestätigung an Kunde | - |
| `mail_template_migration_approved` | Migration OK | Neues Zertifikat |
| `mail_template_migration_rejected` | Migration abgelehnt | - |
| `mail_template_license_revoked` | Key gesperrt | - |

**Alle Templates:**
- Verwenden Jinja für Platzhalter (`{{ object.name }}`, etc.)
- Haben `lang` gesetzt auf `partner_id.lang` für zukünftige i18n
- Subject-Prefix: "[WISSEN BERATUNG] " einheitlich
- Footer-Snippet aus gemeinsamer Mail-Signatur

---

## Crons

In `data/ir_cron_data.xml`:

| Name | Rhythmus | Methode |
|---|---|---|
| WB: License State Update | täglich 01:00 UTC | `wb.license.key._cron_update_states()` |
| WB: Activation Reminders | täglich 08:00 UTC | `wb.license.key._cron_send_activation_reminders()` |
| WB: Renewal Reminders | täglich 09:00 UTC | `wb.license.key._cron_send_renewal_reminders()` |
| WB: Dezember Renewal Invoices | 01.12. 06:00 UTC | `sale.subscription._cron_generate_renewal_invoices()` |
| WB: Trial Cleanup | täglich 02:00 UTC | `wb.license.trial.request._cron_cleanup_trials()` |
| WB: Ticket Cleanup | täglich 03:00 UTC | `wb.activation.ticket._cron_cleanup_expired()` |
| WB: Rate-Limit Cleanup | stündlich | `_cron_cleanup_rate_limits()` |

---

## Security

### Gruppen

In `security/wb_subscription_security.xml`:

| Gruppe | Wer | Rechte |
|---|---|---|
| `group_wb_subscription_user` | Sales-Team | Read auf Keys/Events, Write auf Trials |
| `group_wb_subscription_manager` | Tobias | Full Access + Config + Fernet-Key |

### ACL

In `security/ir.model.access.csv`:

Für jedes Modell:
- User: Read only (außer Trials: Write)
- Manager: Read/Write/Create/Unlink

### Record Rules

**Wichtig:** Keys sind multi-company. Rule: User sieht nur Keys der eigenen Company.

```xml
<record id="wb_license_key_company_rule" model="ir.rule">
    <field name="name">wb.license.key: company</field>
    <field name="model_id" ref="model_wb_license_key"/>
    <field name="domain_force">[('company_id', 'in', company_ids)]</field>
</record>
```

---

## Tests

**Ziel:** 80%+ Coverage auf kritische Pfade.

### `test_key_generator.py`

- `test_generate_public_key_format` — Regex-match
- `test_generate_public_key_unique` — 10k Keys, keine Dupes
- `test_checksum_valid` — Key + manipulierte Checksum → Rejection
- `test_activation_code_format` — 25 Zeichen, 5 Gruppen
- `test_activation_code_no_ambiguous` — keine 0/1/I/O
- `test_bcrypt_roundtrip` — Hash + Verify
- `test_fernet_roundtrip` — Encrypt + Decrypt
- `test_fingerprint_stability` — Same inputs → same fingerprint

### `test_license_activation.py`

- `test_activate_wrong_code` — state bleibt 'issued', Event 'activation_failed' geloggt
- `test_activate_correct_code` — state → 'active', activation_hash leer
- `test_activate_already_activated` — Error 409
- `test_activate_expired_code` — Error 410
- `test_rate_limit_activate` — 6. Call in 1h → Error 429

### `test_ticket_workflow.py`

- `test_ticket_create_state_pending`
- `test_send_otp_changes_state`
- `test_verify_otp_success_reveals_code`
- `test_verify_otp_wrong_increments_attempts`
- `test_verify_otp_5_fails_invalidates_ticket`
- `test_otp_expires_after_10min`
- `test_ticket_expires_after_72h`
- `test_code_hidden_after_10min`

### `test_trial_workflow.py`

- `test_trial_request_captcha_required`
- `test_trial_auto_approve_creates_license`
- `test_trial_rate_limit_same_email`
- `test_trial_rate_limit_same_domain`
- `test_trial_converts_to_paid_revokes_old_key`

---

## Acceptance-Kriterien für Sprint 1 fertig

Sprint 1 ist erst fertig, wenn **alle** folgenden Punkte erfüllt:

- [ ] Alle Modelle angelegt, Constraints greifen, Tests grün
- [ ] Key-Generator erzeugt valide Keys mit korrekter Checksum
- [ ] Activation-Code wird bcrypt-gehasht + optional Fernet-verschlüsselt
- [ ] Alle Controllers erreichbar, Rate-Limiting greift
- [ ] Portal-Ticket-Flow (View + OTP-Send + OTP-Verify + Code-Display) lauffähig
- [ ] Public-Verify-Page zeigt Basis-Info mit reCAPTCHA
- [ ] Admin-UI hat Liste, Form, Smart-Button auf Partner
- [ ] Eine Test-Lizenz kann durch kompletten Workflow (issue → activate → ping → renew → expire) laufen
- [ ] Alle Mail-Templates angelegt mit Jinja-Platzhaltern (Inhalt nicht final, nur Struktur)
- [ ] Cron-Jobs sind aktiv und mindestens 1x durchgelaufen ohne Error
- [ ] README.rst enthält Install + Setup-Anleitung

---

## Offene Punkte (vor Sprint 1 zu entscheiden)

1. **Fernet-Key-Storage:** ir.config_parameter oder ENV-Variable? (Empfehlung: beide, ENV fallback)
2. **reCAPTCHA:** v2 oder v3? Site-Key/Secret wo? (Empfehlung: v3, Parameter in Settings)
3. **Portal-UI Framework:** reines Odoo-Portal oder neues Branding? (Empfehlung: Odoo-Standard + CSS-Override)
4. **Email-Provider:** Brevo via Odoo `fetchmail_outgoing`? (Empfehlung: ja, du hast Brevo eh)

---

## Integration mit wb_license_client

Das Gegenstück-Modul läuft beim Kunden. Siehe `wb_license_client.md` für Details.

Die API-Endpoints hier müssen **semantisch stabil** bleiben — `wb_license_client` v19.0.1 muss auch mit `wb_subscription` v19.0.5 sprechen können.

**Versionierung:**
- API-Version im Response-Header `X-WB-API-Version: 1`
- Breaking Changes → `/api/v2/license/...` (nicht absehbar für Sprint 1)

---

## Abschätzung Aufwand

| Bereich | Stunden |
|---|---|
| Modelle + Constraints | 3 |
| Key-Generator + Tests | 2 |
| Controllers | 2 |
| Portal-UI + Templates | 2 |
| Public-Verify-Page | 1 |
| PDF-Reports (Zertifikat + EULA + Rechnung) | 4 |
| Mail-Templates (alle 18) | 2 |
| Crons | 1 |
| Views (Admin) | 2 |
| Tests | 3 |
| Security | 1 |
| Dokumentation | 1 |
| **Total** | **~24h** |

Realistisch: 3-4 Abende à 6h oder 2 Wochenenden mit Fokus.
