# WB Subscription & License Platform — Architektur

**Version:** 1.6
**Datum:** 27.04.2026
**Autor:** Tobias Wissen (WISSEN BERATUNG)
**Status:** Freigegeben für Umsetzung

**Änderungen in v1.6 (27.04.2026):**
- **License-Issuance entkoppelt von Order-Confirm:** Lizenz wird jetzt
  beim Zahlungseingang erzeugt (`account.move.payment_state` → `paid`),
  nicht mehr bei `sale.order._action_confirm`. Begründung: Folgt
  Odoo-Standard, kein Stripe-spezifischer Code im Modul, Tobias kann
  Provider wechseln ohne wb_subscription anzufassen.
- Order-Endpoint gibt **keine** Stripe-Checkout-URL mehr zurück,
  sondern nur `order_id` mit Hinweis "Rechnung folgt per Mail". Der
  Standard-Rechnungs-Flow von Odoo (mit Payment-Link aus dem
  konfigurierten Provider) übernimmt den Rest.

**Änderungen in v1.5 (24.04.2026):**
- **Review-Fixes vor Sprint 1** eingearbeitet:
  - DECISION #48: Ticket-Code-Storage per Fernet fixiert (statt offener Design-Frage)
  - DECISION #48c: Portal-Ticket an erste IP gebunden (Anti-Forward)
  - `wb.license.info` explizit als `models.Model` (nicht Transient) — sonst kein Cache über Restarts
  - `@license_required` entschärft: bei `unknown` 30 Tage Toleranz statt 7 (Offline-Resilienz)
  - Rate-Limiting bekommt eigenes Modell `wb.rate.limit.entry`
  - CORS pro Endpoint-Typ differenziert (`*` nur für Ping/Activate/Migrate, `*.wissen-beratung.de` für Trial/Order)
- **Strategie-Shift:** Lizenz-Plattform wird als Infrastruktur vor erstem Produkt gebaut.
  - `ELST`-Produkt-Code verworfen, `wb_elster_reports` nicht mehr verfolgt
  - Erstes reales Produkt nach Lizenz-Plattform: `TELE` (Telnyx-Integration)
  - Interims-Produkt-Code `TEST` für End-to-End-Tests während Sprint 1–8

**Änderungen in v1.4:**
- Bestell-Flow: direkter Odoo-Controller statt n8n-Zwischenschritt
  (analog zu bestehendem `/api/web/lead` in `wb_anpassungen`)

**Änderungen in v1.3:**
- Mahn-/Grace-Logik aus Odoo `account_followup` abgeleitet (keine Parallel-Konfig)
- Zahlungsziele via `account.payment.term` pflegbar (auch pro Kunde)
- Design: weißer Hintergrund durchgängig im Zertifikat

**Änderungen in v1.2:**
- **Activation-Code wird NICHT mehr per Email versendet** (Security-Fix)
- Code-Auslieferung via Portal-Download-Ticket (Email enthält nur Link)
- Portal-Zugriff via Email-OTP (kein Account nötig, 6-stelliger Code)
- Neues Modell `wb.activation.ticket` für einmaligen Code-Download
- Threat-Model in Kapitel 11 ergänzt

**Änderungen in v1.1:**
- Activation-Code als zweites Geheimnis eingeführt (bcrypt-Hash in DB)
- Key wird "burned" nach erster Aktivierung (Einmal-Verwendung)
- Zertifikat/Rechnung zeigen nur Public-Key, Code nur in Aktivierungs-Mail

---

## 1. Executive Summary

Zentrale Vertriebs- und Lizenz-Infrastruktur für WISSEN BERATUNG Odoo-Module.
Produkt-agnostisches Backend für aktuell **ELSTER UStVA/ZM** und beliebig
viele künftige Produkte (DATEV, DSGVO, weitere).

**Zwei Vertriebskanäle:**

| Kanal | Modell | Preis-Beispiel |
|---|---|---|
| Odoo App Store | Jahresversion als Einmalkauf (z.B. "ELSTER 2026") | 149 € |
| wissen-beratung.de | Jahresabo mit Lizenzschlüssel, laufend neuste Version | 199 €/Jahr |

**Kernfeatures:**

- Lizenzschlüssel-Verwaltung mit Odoo-Instanz-Bindung
- Zweistufiges Lizenzsystem: Public-Key + Secret Activation-Code (bcrypt)
- Automatisierter Dezember-Renewal-Prozess
- Trial-System mit Lead-Capture (7 Tage)
- PDF-Lizenzzertifikate mit öffentlicher Verifikation
- Kunden-Portal mit Self-Service
- Telegram-Benachrichtigungen an Tobias bei wichtigen Events
- Admin-Dashboard mit MRR/ARR-KPIs

---

## 2. Kernentscheidungen (fixiert)

| # | Entscheidung | Festlegung | Begründung |
|---|---|---|---|
| 1 | Abo-Basis | `sale.subscription` (Odoo Enterprise) | Spart 60-70% Entwicklung |
| 2 | Produkt-Katalog | Standard `product.product` + Zusatzfelder | Integriert in Warenwirtschaft |
| 3 | Notification-Templates | Standard `mail.template` | Odoo-nativ, Jinja-fähig |
| 4 | Key-Format | `WB-{PROD}-{UUID8}-{CHK}` ohne Jahr | Key stabil über Renewals |
| 5 | **Activation-Code** | **25 Zeichen, getrennt vom Key** | **Schutz bei DB-Leak, Einmalverwendung** |
| 6 | **Hash-Verfahren** | **bcrypt(activation_code)** | **Industry-Standard, brute-force-resistent** |
| 7 | **Code-Auslieferung** | **Portal-Download via Ticket + Email-OTP** | **Kanal-Trennung: Email leakt nicht beide Geheimnisse** |
| 8 | Instanz-Bindung | Strikt 1 Odoo-Instanz pro Key | Missbrauch verhindern |
| 9 | Staffelung | Mehrere Instanzen gegen Aufpreis | Monetarisierung Multi-Company |
| 10 | Domain-Umzug | 1x/Jahr, Approval-Workflow durch Tobias | Flexibilität ohne Missbrauch |
| 11 | Trial | 7 Tage, Lead-Capture-Pflicht | Conversion + Qualified Leads |
| 12 | Rechnung Dezember | Draft auto-generiert, manuelle Freigabe | Tobias behält Kontrolle |
| 13 | Grace Period | Abgeleitet aus Odoo-Mahnstufen (`account_followup`) | Keine Parallel-Konfiguration, pflegbar im Standard |
| 14 | Renewal-Datum | Immer 01.01. (anteilig bei Erstkauf) | Planbare Umsatzstruktur |
| 15 | Dokumente | Lizenzzertifikat + EULA + erweiterte Rechnung | Komplette Palette |
| 16 | Verifikation | Zweistufig: öffentlich Basis-Info mit reCAPTCHA, Portal für Details | Usability + Privacy |
| 17 | Design | Weißer Hintergrund mit WB-Logo, Navy + Cyan als Akzente | Seriös, dokumentenwürdig |

---

## 3. Module-Landkarte

```
┌─────────────────────────────────────────────────────────────────────┐
│ DEINE ODOO (wissen-beratung.de) — VERTRIEBS-BACKEND                 │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ODOO-STANDARD (nutzen, nichts neu bauen):                          │
│  ├─ product.product          Produkt-Katalog                        │
│  ├─ sale.subscription        Abo-Management                         │
│  ├─ sale.order               Bestellabwicklung                      │
│  ├─ account.move             Rechnungen                             │
│  ├─ account.payment          Zahlungsabwicklung                     │
│  ├─ account.payment.term     Zahlungsziele (pro Kunde individuell)  │
│  ├─ account_followup         Mahnstufen & Mahn-Workflow             │
│  ├─ res.partner              Kunden                                 │
│  ├─ mail.template            E-Mail-Vorlagen                        │
│  ├─ ir.cron                  Zeitgesteuerte Jobs                    │
│  └─ mail.thread/activity     Chatter                                │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ wb_subscription  (eigenes Modul)                             │  │
│  ├──────────────────────────────────────────────────────────────┤  │
│  │ EIGENE MODELLE:                                              │  │
│  │  ├─ wb.license.key              Der Schlüssel + Hash         │  │
│  │  ├─ wb.license.event            Audit-Log                    │  │
│  │  ├─ wb.license.migration        Umzugs-Anträge               │  │
│  │  ├─ wb.license.trial.request    Lead-Capture-Trials          │  │
│  │  ├─ wb.notification.log         Benachrichtigungs-Log        │  │
│  │  └─ wb.license.tag              Kategorisierung              │  │
│  │                                                              │  │
│  │ ERWEITERUNGEN auf Odoo-Standards:                            │  │
│  │  ├─ product.product.wb_*        Lizenz-Produkt-Felder        │  │
│  │  ├─ sale.subscription.wb_*      Verknüpfung zu Keys          │  │
│  │  └─ res.partner.wb_license_ids  Keys am Kunden               │  │
│  │                                                              │  │
│  │ HTTP-ENDPOINTS:                                              │  │
│  │  ├─ POST /api/wb_subscription/order Bestellung von Webseite  │  │
│  │  ├─ POST /api/license/check         Ping vom Kunden          │  │
│  │  ├─ POST /api/license/activate      Erstaktivierung+Code     │  │
│  │  ├─ POST /api/license/trial         Trial-Anfrage            │  │
│  │  ├─ POST /api/license/migrate       Umzug beantragen         │  │
│  │  ├─ GET  /activate/<ticket>         Portal: Code-Download    │  │
│  │  ├─ POST /activate/<ticket>/verify  Portal: Email-OTP        │  │
│  │  └─ GET  /license/verify/<key>      Öffentl. Verifikation    │  │
│  │                                                              │  │
│  │ DOKUMENTEN-RENDERER:                                         │  │
│  │  ├─ Lizenzzertifikat (QWeb → PDF)                            │  │
│  │  ├─ EULA (QWeb → PDF, aus Jinja-Template)                    │  │
│  │  └─ Rechnungs-Erweiterung (existing report override)         │  │
│  │                                                              │  │
│  │ DASHBOARDS & UI:                                             │  │
│  │  ├─ Admin-Dashboard (MRR/ARR/Events)                         │  │
│  │  ├─ Kundenportal-Erweiterung                                 │  │
│  │  └─ Verifikations-Page (public)                              │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                               ▲
                               │ HTTPS
                               │
┌──────────────────────────────┼──────────────────────────────────────┐
│ KUNDEN-ODOO                                                         │
├──────────────────────────────┼──────────────────────────────────────┤
│                              │                                      │
│  ┌──────────────────────────────────────────────────────────┐      │
│  │ wb_license_client  (gemeinsames Basis-Modul)             │      │
│  ├──────────────────────────────────────────────────────────┤      │
│  │ AUFGABEN:                                                │      │
│  │  ├─ Key-Speicher (ir.config_parameter)                   │      │
│  │  ├─ Activation-Wizard (Key + Code eingeben)              │      │
│  │  ├─ Täglicher Ping an /api/license/check                 │      │
│  │  ├─ Caching des Status (offline tolerant)                │      │
│  │  ├─ UI-Banner für Ablauf / Grace                         │      │
│  │  ├─ Feature-Gates (decorator @license_required)          │      │
│  │  └─ Migration-Request-UI                                 │      │
│  │                                                          │      │
│  │ API:                                                     │      │
│  │    env['wb.license.client'].check_license('ELST')        │      │
│  │    → True/False (mit Caching)                            │      │
│  └──────────────────────────────────────────────────────────┘      │
│                         ▲                                           │
│                         │ depends                                   │
│  ┌──────────────────────┴───────────────────────────────────┐      │
│  │ Produkt-Module (ELSTER, DATEV, DSGVO, …)                 │      │
│  │ - prüfen bei kritischen Operationen Lizenz-Status         │      │
│  │ - sind selbst lizenz-agnostisch beim Code                 │      │
│  └──────────────────────────────────────────────────────────┘      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 4. Key-Format, Activation-Code & Algorithmus

### 4.1 Public Key — Format

```
WB-{PROD}-{UUID8}{CHK}

Beispiel: WB-ELST-a3f28c919K

 WB         → Konstanter Präfix
 ELST       → 4-stelliger Produkt-Code (uppercase)
 a3f28c91   → erste 8 Zeichen einer UUID4 (lowercase hex)
 9K         → 2-stellige Base32-Checksum (direkt angehängt, kein Bindestrich)

Gesamt: 17 Zeichen mit Bindestrichen, am Telefon gut vorlesbar.
```

**Designprinzip:** Die Checksum ist ein Implementierungsdetail zur
Tippfehler-Erkennung, kein logisches Feld. Sie ist deshalb direkt an die
UUID angehängt statt als eigene Gruppe (analog Kreditkarten-Luhn-Prüfziffer,
IBAN-Prüfziffer etc.).

**Verwendung des Public Key:**
- Angedruckt auf Zertifikat (PDF)
- Aufgeführt auf Rechnung
- In täglichen Ping-Requests (identifiziert die Lizenz)
- Öffentlich verifizierbar via `/license/verify/<key>`

### 4.2 Activation-Code — Format

```
XXXXX-XXXXX-XXXXX-XXXXX-XXXXX

Beispiel: 7H3K9-M4P2N-RQ8T2-W5X7Y-A9B3F

 Zeichen:  Base32 ohne Verwechslungsgefahr (A-H, J-N, P-Z, 2-9)
           = 32 Symbole, aber KEIN 0, 1, I, O (Tippfehler-Quelle)
 Länge:    25 Zeichen + 4 Bindestriche = 29 Total
 Entropie: 32^25 ≈ 1.4 × 10^37 Möglichkeiten
```

**Verwendung des Activation-Codes:**
- Wird **einmalig** bei Auslieferung per E-Mail verschickt
- Wird bei Erst-Aktivierung gegen DB-Hash geprüft
- Wird **nach erfolgreicher Aktivierung gelöscht** (Einmalverwendung)
- Wird **NICHT** auf Zertifikat/Rechnung angedruckt
- Bei Migration: KEIN neuer Code nötig — Migration läuft über Approval-Workflow

### 4.3 Hash-Speicherung (bcrypt)

Im Datenmodell wird **nicht** der Klartext-Code gespeichert, sondern ein
bcrypt-Hash. Auch wenn die Datenbank komplett abgezogen würde, könnten
Angreifer die Keys nicht aktivieren (weil sie die Codes nicht kennen).

```python
import bcrypt

def hash_activation_code(code: str) -> bytes:
    """bcrypt-Hash erzeugen. 12 Runden ≈ 200ms auf typischer Hardware."""
    return bcrypt.hashpw(code.encode('utf-8'), bcrypt.gensalt(rounds=12))

def verify_activation_code(code: str, stored_hash: bytes) -> bool:
    return bcrypt.checkpw(code.encode('utf-8'), stored_hash)
```

Nach erfolgreicher Aktivierung:
```python
license.activation_hash = False  # "verbrannt"
license.activated_at = fields.Datetime.now()
```

Ab diesem Punkt kann der Code nicht mehr verwendet werden.

### 4.4 Produkt-Code-Registry

| Code | Produkt | Bemerkung |
|---|---|---|
| `ELST` | ELSTER Reports | UStVA + ZM |
| `DATV` | DATEV Export | Geplant |
| `DSGV` | DSGVO Auskunft | Geplant |
| `TELE` | TELnyx Tools | Geplant |

Der Code wird am `product.product.wb_technical_code`-Feld gepflegt.

### 4.5 Checksum-Algorithmus (Public Key)

```python
def compute_checksum(data: str) -> str:
    """
    Berechnet 2-stellige Base32-Checksum über UPPERCASE(data).
    """
    total = sum(ord(c) for c in data.upper()) % 1024
    alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'
    return alphabet[total // 32] + alphabet[total % 32]


def generate_public_key(product_code: str) -> str:
    import uuid
    uuid_short = uuid.uuid4().hex[:8]
    payload = f"WB-{product_code.upper()}-{uuid_short}"
    checksum = compute_checksum(payload)
    # Checksum direkt an UUID angehängt, nicht als eigene Gruppe
    return f"{payload}{checksum}"


def generate_activation_code() -> str:
    """Erzeugt 25-stelligen Code in 5er-Gruppen."""
    import secrets
    alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'  # ohne 0,1,I,O
    raw = ''.join(secrets.choice(alphabet) for _ in range(25))
    return '-'.join(raw[i:i+5] for i in range(0, 25, 5))


def generate_license(product_code: str) -> tuple[str, str, bytes]:
    """Erzeugt Public Key, Activation Code und Hash."""
    pub_key = generate_public_key(product_code)
    act_code = generate_activation_code()
    act_hash = hash_activation_code(act_code)
    return pub_key, act_code, act_hash
```

### 4.6 Validierung auf Client-Seite (vor API-Call)

```python
def validate_key_format(key: str) -> bool:
    import re
    # Checksum hängt direkt an UUID, kein Bindestrich dazwischen
    m = re.match(r'^WB-([A-Z]{4})-([a-f0-9]{8})([A-Z2-7]{2})$', key)
    if not m:
        return False
    product_code, uuid_short, checksum = m.groups()
    payload = f"WB-{product_code}-{uuid_short}"
    return compute_checksum(payload) == checksum


def validate_activation_code_format(code: str) -> bool:
    import re
    return bool(re.match(r'^[A-HJ-NP-Z2-9]{5}(-[A-HJ-NP-Z2-9]{5}){4}$', code))
```

### 4.7 Code-Auslieferung via Portal-Ticket (NEU v1.2)

**Prinzip: Channel-Separation.**

Der Activation-Code verlässt den Server **niemals** im Klartext per Email.
Stattdessen:

1. Beim Kauf wird ein **Activation-Ticket** erzeugt (UUID, kein Secret)
2. Email an Kunden enthält nur den Ticket-Link: `https://.../activate/<ticket_token>`
3. Kunde klickt Link → Portal-Seite zeigt Email-Adresse und fordert OTP
4. Server schickt 6-stelligen **Email-OTP** an die beim Kauf hinterlegte Email
5. Kunde gibt OTP ein → Server prüft → Activation-Code wird angezeigt
6. Code ist **10 Minuten sichtbar**, danach wird die Seite "verbrannt"

**Warum das funktioniert:**
- Link allein = nutzlos (braucht Email-Zugang)
- Email-Zugang allein = nutzlos (braucht Ticket-Link)
- Beides zusammen = Zugriff, aber nur einmalig und zeitlich begrenzt

**Ticket-Token-Format:**
```
TCKT-{UUID22}

Beispiel: TCKT-x9f2k8m4p7n3q5w1r6t8y0

 UUID22  = base62-kodierte UUID4 (22 Zeichen)
 Gesamt: 27 Zeichen inkl. Präfix
```

Grund für eigenes Format (nicht nochmal WB-Schema): Tickets sind
**temporär** (72h), nie auf Papier, und sollen sich visuell klar vom
permanenten Public Key unterscheiden.

**Email-OTP-Format:**
```
6-stellige Ziffer, kryptografisch zufällig

Beispiel: 384217

 Gültigkeit: 10 Minuten ab Versand
 Max 5 Fehlversuche pro Ticket → Ticket wird invalidiert
```

```python
def generate_ticket_token() -> str:
    """27-stelliges Ticket mit TCKT-Prefix."""
    import uuid
    import base64
    raw = uuid.uuid4().bytes
    # base62 für URL-Safety ohne Padding
    encoded = base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')
    return f"TCKT-{encoded}"

def generate_email_otp() -> str:
    """6-stelliger Email-OTP."""
    import secrets
    return ''.join(str(secrets.randbelow(10)) for _ in range(6))
```

---

## 5. Datenmodell (finale Version)

### 5.1 Erweiterungen auf `product.product`

```python
wb_is_license_product    Boolean       default=False
wb_technical_code        Char(4)       # 'ELST', 'DATV', ...
wb_module_technical_name Char          # 'wb_elster_reports'
wb_instance_limit        Integer       default=1
wb_extra_instance_price  Float
wb_trial_days            Integer       default=7
wb_eula_template_id      M2O           # mail.template für EULA
wb_certificate_template_id M2O         # ir.actions.report für Zertifikat
```

### 5.2 Erweiterungen auf `sale.subscription`

```python
wb_license_key_ids       O2M wb.license.key  # inverse='subscription_id'
wb_is_license_sub        Boolean computed
```

### 5.3 Erweiterungen auf `res.partner`

```python
wb_license_key_ids       O2M wb.license.key
wb_license_count         Integer computed
```

### 5.4 `wb.license.key` (Hauptmodell) — MIT ACTIVATION

```python
# --- Identität ---
name                     Char       # = Public Key, read-only nach create
display_name             computed

# --- ACTIVATION (NEU v1.1) ---
activation_hash          Binary     # bcrypt-Hash des Codes (in DB)
activation_hash_method   Char       default='bcrypt'  # future-proof
activated_at             Datetime   # wann erfolgreich aktiviert
activated_fingerprint    Char       # SHA256(domain+db_uuid+timestamp)
activation_expires_at    Datetime   # Code-Verfallsdatum (z.B. +90 Tage nach Kauf)

# --- Beziehungen ---
product_id               M2O product.product   required
partner_id               M2O res.partner       required
subscription_id          M2O sale.subscription # None wenn Trial

# --- Status ---
state                    Selection:
    'issued'             Key erstellt, noch nicht aktiviert (NEU)
    'trial'              Lead-Capture-Trial, 7 Tage
    'active'             Aktiviert + bezahlt
    'grace'              Rechnung überfällig
    'expired'            Abgelaufen
    'revoked'            Manuell gesperrt
    'cancelled'          Gekündigt

# --- Laufzeit ---
valid_from               Date      required
valid_to                 Date      required
grace_until              Date      computed aus partner.followup_status
                                   + letzte Mahnstufe mit delay
                                   # Bleibt leer wenn Rechnung bezahlt
last_renewal_date        Date

# --- Instanz-Bindung ---
bound_domain             Char
bound_db_uuid            Char
instance_limit           Integer
instance_current         Integer

# --- Monitoring ---
last_seen_at             Datetime
last_seen_ip             Char
last_seen_user_agent     Char
ping_count_total         Integer

# --- Historie ---
event_ids                O2M wb.license.event
migration_ids            O2M wb.license.migration.request
certificate_ids          O2M ir.attachment

# --- Meta ---
tag_ids                  M2M wb.license.tag
note                     Text
internal_note            Text
company_id               M2O res.company
```

**State-Übergänge** (aktualisiert mit 'issued'):

```
       [create from sale]
              │
              ▼
          ┌────────┐       [activate() success]
          │ issued │ ───────────────────────────►┐
          └────────┘                             │
                                                 │
          [create trial]                         │
              │                                  │
              ▼                                  │
          ┌───────┐     [expire after 7d]        │
          │ trial │ ──────────────►  [expired]   │
          └───┬───┘     [convert]                │
              │                                  │
              ▼                                  ▼
         ┌────────┐                          ┌────────┐
         │ active │ ◄────────────────────────┤ active │
         └───┬────┘    [payment_in renewal]  │        │
             │                               └────────┘
             │ [valid_to + no payment]
             ▼
         ┌───────┐
         │ grace │  ... (rest wie vorher)
         └───────┘
```

### 5.5 `wb.license.event` (Audit-Log)

```python
license_id               M2O
event_type               Selection:
    'key_generated'      Public Key + Code erzeugt (NEU)
    'activation'         Erstaktivierung mit Code
    'activation_failed'  Falscher Code versucht (NEU)
    'ping'
    'renewal'
    'trial_started'
    'trial_converted'
    'trial_expired'
    'grace_started'
    'grace_ended'
    'migration_requested'
    'migration_approved'
    'migration_rejected'
    'revoked'
    'reactivated'
timestamp                Datetime
ip_address               Char
user_agent               Char
domain                   Char
db_uuid                  Char
details                  Json
created_by               M2O res.users
```

### 5.6 `wb.license.migration.request`

(unverändert zu v1.0)

### 5.7 `wb.license.trial.request`

(unverändert zu v1.0)

### 5.8 `wb.notification.log`

(unverändert zu v1.0)

### 5.9 `wb.license.tag`

(unverändert zu v1.0)

---

## 6. HTTP-API-Spezifikation

### 6.1 `POST /api/license/check` — Täglicher Ping

(unverändert zu v1.0 — Public Key reicht für Ping)

### 6.2 `POST /api/license/activate` — Erstaktivierung (MIT CODE)

**Zweck:** Nach Key+Code-Eingabe im Client erstmalig Bindung setzen.

**Request:**
```json
{
  "key": "WB-ELST-a3f28c919K",
  "activation_code": "7H3K9-M4P2N-RQ8T2-W5X7Y-A9B3F",
  "domain": "odoo.mueller-gmbh.de",
  "db_uuid": "a7b3c2d1-9e8f-4b2c-8a3d-1f5e9c7b2a0d",
  "email": "admin@mueller-gmbh.de",
  "client_version": "19.0.1.2.0"
}
```

**Server-Logik:**
```python
@route('/api/license/activate', auth='public', methods=['POST'])
def activate(self, **kw):
    key = kw['key']
    code = kw['activation_code']

    # 1. Format prüfen (ohne DB)
    if not validate_key_format(key):
        return json({'error': 'INVALID_KEY_FORMAT'}, 400)
    if not validate_activation_code_format(code):
        return json({'error': 'INVALID_CODE_FORMAT'}, 400)

    # 2. License finden
    license = env['wb.license.key'].sudo().search([('name', '=', key)], limit=1)
    if not license:
        return json({'error': 'KEY_NOT_FOUND'}, 404)

    # 3. Bereits aktiviert?
    if license.activated_at:
        log_event(license, 'activation_failed', reason='already_activated')
        return json({'error': 'ALREADY_ACTIVATED',
                     'bound_to': license.bound_domain}, 409)

    # 4. Code abgelaufen?
    if license.activation_expires_at and \
       license.activation_expires_at < fields.Datetime.now():
        return json({'error': 'ACTIVATION_EXPIRED'}, 410)

    # 5. Code gegen Hash prüfen (bcrypt)
    if not verify_activation_code(code, license.activation_hash):
        log_event(license, 'activation_failed', reason='wrong_code',
                  ip=request.httprequest.remote_addr)
        # Rate-Limit: nach 5 Fehlversuchen für 1h sperren
        return json({'error': 'WRONG_CODE'}, 401)

    # 6. Erfolg: Activation vollziehen
    fingerprint = compute_fingerprint(
        domain=kw['domain'], db_uuid=kw['db_uuid'])
    license.write({
        'bound_domain': kw['domain'],
        'bound_db_uuid': kw['db_uuid'],
        'activated_at': fields.Datetime.now(),
        'activated_fingerprint': fingerprint,
        'activation_hash': False,  # "verbrannt"
        'state': 'active' if license.state == 'issued' else license.state,
    })
    log_event(license, 'activation',
              ip=request.httprequest.remote_addr,
              domain=kw['domain'], db_uuid=kw['db_uuid'])

    return json({
        'status': 'active',
        'bound_domain': license.bound_domain,
        'valid_to': license.valid_to.isoformat(),
        'message': 'Willkommen! Ihre Lizenz ist jetzt aktiv.',
    })
```

**Responses:**

| Code | Error | Bedeutung |
|---|---|---|
| 200 | - | Aktivierung erfolgreich |
| 400 | `INVALID_KEY_FORMAT` / `INVALID_CODE_FORMAT` | Format stimmt nicht |
| 401 | `WRONG_CODE` | Code falsch |
| 404 | `KEY_NOT_FOUND` | Key existiert nicht |
| 409 | `ALREADY_ACTIVATED` | Key wurde schon aktiviert |
| 410 | `ACTIVATION_EXPIRED` | Code-Gültigkeit abgelaufen |
| 429 | `TOO_MANY_ATTEMPTS` | Rate-Limit (5 Fehlversuche/h) |

### 6.3 `POST /api/license/trial`

(unverändert — Trial-Keys haben vorab-aktivierten Code, siehe Workflow 9.2)

### 6.4 `POST /api/license/migrate`

(unverändert — keine Code-Prüfung nötig, da Key bereits aktiviert)

### 6.5 `GET /license/verify/<key>`

(unverändert — zeigt nur Public Key-Info)

---

## 7. Dokumente

### 7.1 Lizenzzertifikat (PDF)

**Wichtig v1.1:** Das Zertifikat zeigt **NUR den Public Key**, NICHT den
Activation-Code! Der Code ist ein Geheimnis und wird ausschließlich in
der Aktivierungs-Email verschickt.

```
╔═══════════════════════════════════════════════════════════╗
║                     [WB-Logo groß]                        ║
║                                                           ║
║              LIZENZBESTÄTIGUNG / LICENSE                  ║
║                                                           ║
║  Zertifikat-Nr:    LIZ-2026-00142                        ║
║  ...                                                      ║
║                                                           ║
║  ═══════════════ Lizenzschlüssel ═════════              ║
║                                                           ║
║    ┌─────────────────────────────────────────┐          ║
║    │  WB-ELST-a3f28c919K                    │          ║
║    └─────────────────────────────────────────┘          ║
║                                                           ║
║    (Activation-Code wurde separat per Email versendet)   ║
║                                                           ║
║                                    [QR-Code]              ║
║                                                           ║
╚═══════════════════════════════════════════════════════════╝
```

**Design-Prinzipien:**
- **Weißer Hintergrund durchgängig** (kein farbiger Header, kein farbiger Footer) — wirkt seriös und dokumentenwürdig
- WB-Logo oben zentriert auf weiß
- **Navy #1D3C6E** nur für Überschriften und Trennlinien
- **Cyan #00C8E8** nur als sparsame Akzente (z.B. dünner Rahmen um Key-Box)
- 1 Seite, klares Layout, viel Weißraum
- QR-Code unten rechts → Verifikations-URL
- Dokument-Footer mit Zertifikat-Nr, Ausgabe-Datum, klein und grau

Rest der Inhalte (Kundendaten, Laufzeit, Produkt) unverändert zu v1.0.

### 7.2 EULA (PDF)

(unverändert zu v1.0)

### 7.3 Rechnung (Odoo Standard + Erweiterung)

Pro Lizenz-Produkt-Zeile wird angezeigt:
- Lizenzschlüssel (Public)
- Gebundene Domain
- Laufzeit

Der Activation-Code wird **nicht** auf der Rechnung ausgewiesen.

### 7.4 Aktivierungs-Mail (NEU v1.1, Security-Fix v1.2)

Separates E-Mail-Template `wb_activation_instructions` das den
**Ticket-Link zum Portal-Download** enthält — aber **NIEMALS** den
Activation-Code selbst. Kanal-Trennung.

**Inhalt (Entwurf):**
```
Betreff: Ihr Lizenzschlüssel für [Produkt] — Aktivierung in 2 Schritten

Guten Tag [Kundenname],

vielen Dank für Ihren Kauf von [Produkt].

Ihre Lizenz ist fast aktiv. Um Ihren Aktivierungs-Code abzurufen,
folgen Sie bitte diesem Link:

  ┌──────────────────────────────────────────────┐
  │  Aktivierungs-Code abrufen:                  │
  │  https://wissen-beratung.de/activate/        │
  │  TCKT-x9f2k8m4p7n3q5w1r6t8y0                 │
  └──────────────────────────────────────────────┘

Nach Klick auf den Link:

 1. Wir senden einen 6-stelligen Bestätigungs-Code an diese
    E-Mail-Adresse ([email])
 2. Sie geben den 6-stelligen Code auf der Seite ein
 3. Ihr Aktivierungs-Code wird für 10 Minuten angezeigt
 4. Code in Ihrer Odoo-Instanz eingeben und aktivieren

Zu Ihrer Sicherheit erhalten Sie den Aktivierungs-Code
ausschließlich im geschützten Bereich — niemals per E-Mail.

Wichtig:
• Link ist 72 Stunden gültig
• Der Aktivierungs-Code kann nur EINMAL verwendet werden
• Die Aktivierung ist bis zum [EXPIRES_AT] möglich

Ihr Lizenzschlüssel (bereits auf dem Zertifikat):
  WB-ELST-a3f28c919K

Bei Fragen: support@wissen-beratung.de

Viele Grüße
Tobias Wissen
WISSEN BERATUNG
```

**Sicherheits-Eigenschaften:**
- E-Mail enthält **nur den öffentlichen Key** und **den Ticket-Link**
- Der Aktivierungs-Code selbst ist **niemals** in einer E-Mail (Klartext)
- Wer die E-Mail abfängt hat nur den Key (öffentlich auf Zert/Rechnung)
  und den Link — kommt aber nicht an den Code, weil das Portal noch
  die Email-OTP abfragt
- Wer den Email-Account übernimmt kann Code abrufen — aber das ist
  unvermeidbar bei jeder Email-basierten Auth (gleicher Risiko-Level
  wie "Password-Reset per Mail")

---

## 8. Benachrichtigungs-Matrix

Aktualisiert mit neuen Templates:

| Event | Template | Empfänger | Channel | Anhang |
|---|---|---|---|---|
| Trial aktiviert | `wb_trial_activated` | Lead | Email | Key+Code inline |
| Trial Tag 5 | `wb_trial_ending_soon` | Lead | Email | - |
| Trial expired | `wb_trial_expired` | Lead | Email | - |
| Kauf+Zahlung empfangen | `wb_payment_received` | Kunde | Email+Telegram | **Zertifikat + EULA** |
| **Activation-Instructions** | **`wb_activation_instructions`** | **Kunde** | **Email** | **-** |
| **Activation-Reminder 7d** | **`wb_activation_reminder_7d`** | **Kunde** | **Email** | **-** |
| **Activation-Reminder 30d** | **`wb_activation_reminder_30d`** | **Kunde** | **Email** | **-** |
| Renewal 60 Tage | `wb_renewal_reminder_60d` | Kunde | Email | - |
| Renewal 30 Tage | `wb_renewal_reminder_30d` | Kunde | Email | - |
| Rechnung versendet (Dez) | `wb_renewal_invoice` | Kunde | Email | Rechnungs-PDF |
| Zahlungserinnerung/Mahnung | **Odoo `account_followup` Standard** | Kunde | Email | Mahnung (Odoo) |
| Grace gestartet (wenn letzte Mahnstufe erreicht) | `wb_grace_started` | Kunde | Email+Telegram | - |
| Grace letzter Tag | `wb_grace_last_day` | Kunde | Email | - |
| Lizenz expired | `wb_license_expired` | Kunde | Email | - |
| **Activation-Failed (Alert)** | **`wb_activation_failed_alert`** | **Tobias** | **Telegram** | **-** |
| Migration angefragt | `wb_migration_requested` | Kunde+Tobias | Email | - |
| Migration approved | `wb_migration_approved` | Kunde | Email | Neues Zertifikat |
| Migration rejected | `wb_migration_rejected` | Kunde | Email | - |
| Lizenz revoked | `wb_license_revoked` | Kunde+Tobias | Email+Telegram | - |

**Neu in v1.1** (fett markiert):
- Activation-Instructions: separate Mail für Code (nicht zusammen mit Zertifikat)
- Activation-Reminder: wenn Key nach 7/30 Tagen noch nicht aktiviert
- Activation-Failed-Alert: bei 5+ Fehlversuchen → Tobias wird benachrichtigt

### 8.1 Integration mit Odoo-Mahnsystem (v1.2)

**Prinzip:** Wir erfinden kein eigenes Mahnungs-System. Odoo's
`account_followup`-Modul übernimmt die komplette Mahn-Logik.

**Datenfluss:**

```
sale.order → account.move (Rechnung)
                │
                │ hat: account.payment.term (Zahlungsziel)
                │ hat: invoice_date_due
                ▼
       Bei Überfälligkeit → Odoo automatic:
                │
                ▼
       partner.followup_status
                │
                ├─ 'no_action_needed'      → Lizenz: active
                ├─ 'in_need_of_action'     → Lizenz: active (Zahlungserinnerung läuft)
                └─ 'with_overdue_invoices' → Lizenz: grace startet
                        │
                        ▼
                Letzte Mahnstufe erreicht
                        │
                        ▼
                Lizenz: expired
                wb.license.key.state = 'expired'
```

**Konfigurierbar im Odoo-Standard:**

| Wo konfigurieren | Was |
|---|---|
| **Einstellungen → Buchhaltung → Zahlungsbedingungen** | Z.B. "30 Tage netto", "14 Tage 2% Skonto, 30 Tage netto" — bestimmt `invoice_date_due` |
| **Buchhaltung → Konfiguration → Mahnstufen** | Stufen definieren: Tage nach Fälligkeit, Mahnschreiben, `manual_action_note` |
| **Kunden-Stammdaten** | Pro Kunde eigene Zahlungsbedingung möglich (z.B. Großkunden: 60 Tage) |

**Was der Kunde erlebt:**

```
Rechnung fällig 15.01.
   │
   │ Odoo schickt Mahnschreiben (aus deinen Mahnstufen)
   │  z.B. Stufe 1 nach 7 Tagen  → "Freundliche Erinnerung"
   │       Stufe 2 nach 21 Tagen → "2. Mahnung"
   │       Stufe 3 nach 35 Tagen → "Letzte Mahnung"
   │
   ▼
Bei letzter Mahnstufe erreicht (z.B. 35 Tage):
   → Lizenz-Status: grace
   → Email wb_grace_started
   → Telegram an Tobias
   │
   ▼
Stufe-3 Deadline + z.B. 7 weitere Tage (auch in Odoo konfigurierbar):
   → Lizenz-Status: expired
   → Email wb_license_expired
   → Modul-Features beim Kunden deaktiviert
```

**Welche Daten nutzt unser License-Modul aus dem Followup-System:**

```python
# Statt harter 14-Tage-Logik:
def _compute_grace_status(self):
    for license in self:
        partner = license.partner_id
        if partner.followup_status == 'no_action_needed':
            license.state = 'active'  # nothing to do
        elif partner.followup_status == 'in_need_of_action':
            license.state = 'active'  # Mahnung läuft, aber noch kein Grace
        elif partner.followup_status == 'with_overdue_invoices':
            # letzte Mahnstufe erreicht, Lizenz in Grace
            license.state = 'grace'
            # grace_until: aus letzter followup.line + delay
            last_line = partner.followup_line_id  # aktuelle Stufe
            license.grace_until = fields.Date.today() + \
                timedelta(days=last_line.delay or 7)
```

**Vorteile dieser Architektur:**
- Keine Dopplung: Mahnfristen werden nur einmal in Odoo-Stammdaten gepflegt
- Zahlungsziele individuell pro Kunde möglich (z.B. Konzern: 60 Tage, KMU: 14 Tage)
- Mahntexte & Corporate Wording werden in Odoo-Standard gepflegt
- DATEV-Export, Mahnjournal etc. funktionieren automatisch mit
- Bei Ist-Versteuerung (§ 20 UStG): Odoo rechnet das korrekt mit

---

## 9. Workflows (Step-by-Step)

### 9.1 Erstkauf über Webseite — MIT ACTIVATION (v1.1)

```
1. Kunde bestellt auf wissen-beratung.de
   → Formular-Felder: Firma, Adresse, USt-ID, Domain, Produkt

2. Formular-POST direkt an Odoo-Controller
   → `/api/wb_subscription/order` (analog zu bestehendem `/api/web/lead`)
   → Authentifizierung via X-API-Key Header (in System-Parameter)
   → CORS-Whitelist für wissen-beratung.de
   → Controller legt direkt an:
     - res.partner (anlegen oder matchen via E-Mail)
     - sale.order (Draft-State)
   → Response: order_id + Hinweis "Rechnung folgt per E-Mail"
   → KEINE Provider-spezifische URL — Folge: kein Stripe-Coupling im Modul

3. Tobias bestätigt sale.order in Odoo (oder Auto-Confirm-Regel greift)
   → Standard-Rechnung wird als Draft erzeugt
   → Versand der Rechnung mit Payment-Link aus dem konfigurierten
     Payment-Provider (Stripe/SEPA/Sofortüberweisung — egal welcher)

4. Kunde zahlt über den Link
   → Provider-Webhook → Odoo registriert account.payment
   → account.move.payment_state wechselt auf 'paid' / 'in_payment'

5. account.move.write Hook fängt den State-Wechsel ab:
   → ruft sale.order._wb_issue_license_keys() auf
   → wb.license.key generiert:
     - state = 'issued'  (NEU: noch nicht aktiviert!)
     - Public Key: WB-ELST-a3f28c919K
     - Activation Code: 7H3K9-M4P2N-... (nur im RAM, direkt gehashed)
     - activation_hash = bcrypt(code)
     - activation_expires_at = heute + 90 Tage
     - bound_domain = aus Bestellung
     - bound_db_uuid = null
   → wb.activation.ticket erzeugt (72h gültig)
   → Event 'key_generated' geloggt (ohne Code!)
   → Zertifikat-PDF generiert (nur Public Key)
   → 2 E-Mails parallel:
     (a) wb_payment_received mit Zertifikat + EULA (nur Public Key)
     (b) wb_activation_instructions mit Ticket-Link (KEIN Code!)
   → Telegram an Tobias: "💰 Neuer Kauf: Müller GmbH, ELSTER, 199€"
   → Activation-Code wird nach DB-Speicherung aus Server-RAM gelöscht

6. Kunde klickt Ticket-Link in Email
   → Portal-Seite: /activate/<ticket_token>
   → Portal fordert Email-OTP
   → Server sendet 6-stellige OTP an hinterlegte Email (10 Min gültig)
   → Kunde gibt OTP ein
   → Portal zeigt Activation-Code für 10 Minuten

7. Kunde öffnet Odoo-Instanz → "Lizenz aktivieren"-Wizard
   → Gibt Public Key + Activation Code ein
   → Client-Modul POSTet /api/license/activate

8. Server:
   → Prüft Format beider Werte
   → Lädt Key aus DB (activation_hash vorhanden)
   → bcrypt.checkpw(code, activation_hash) → True
   → Bindet Domain + DB-UUID
   → activation_hash = False (verbrannt)
   → state: 'issued' → 'active'
   → Event 'activation' geloggt

9. Cron 7 Tage später:
   → Wenn state='issued' (nicht aktiviert):
     → Mail wb_activation_reminder_7d

10. Cron 30 Tage später:
    → Wenn state='issued':
      → Mail wb_activation_reminder_30d + Telegram an Tobias

10. Cron 90 Tage später:
    → Wenn state='issued':
      → activation_expires_at ist erreicht
      → state='issued' bleibt, aber Aktivierung scheitert ab jetzt
      → Mail: "Bitte kontaktieren Sie uns zur Reaktivierung"
     → state='issued' bleibt, aber Aktivierung scheitert ab jetzt
     → Mail: "Bitte kontaktieren Sie uns zur Reaktivierung"
```

### 9.2 Trial-Anfrage — OHNE CODE-KOMPLEXITÄT

Für Trials machen wir es einfacher: Der Trial-Key ist bereits
**vor-aktiviert**, braucht keinen separaten Code. Begründung:

- Trial läuft nur 7 Tage — Zeitfenster zu klein für "Code verlegt"
- Friction-Reduction ist bei Trial kritisch (Conversion)
- Sicherheitsrisiko minimal (kein Umsatz-Verlust bei Trial-Missbrauch)

```
1. Kunde füllt Trial-Formular (E-Mail + Firma)
2. POST /api/license/trial
3. Server:
   → wb.license.key mit:
     - state = 'trial'
     - activation_hash = null (kein Code erforderlich!)
     - valid_from = heute, valid_to = heute+7
   → Mail wb_trial_activated mit Key inline

4. Kunde: öffnet Modul → Wizard akzeptiert Trial-Keys OHNE Code
   → Normale Bindung (Domain+DB-UUID)

5. Bei Conversion zu Paid:
   → Neuer Regular-Key generiert mit Code
   → Trial-Key wird revoked
```

### 9.3 Dezember-Renewal (mit Mahnstufen-Integration v1.2)

```
1. Cron 01.12., 06:00 UTC:
   → Für alle sale.subscription mit:
     - state='open'
     - auto_renew=True
     - wb_is_license_sub=True
   → account.move erzeugt, state='draft'
     - invoice_date = 15.12.
     - payment_term_id = aus Kunde-Stammdaten
       (oder Default-Zahlungsbedingung)
     - invoice_date_due = wird automatisch berechnet
     - Zeilen: alle Abo-Zeilen + Lizenzschlüssel als Hinweis

2. Tobias 10.-15.12.:
   → Dashboard zeigt "X Rechnungen zur Freigabe"
   → Prüft auffällige (Storno, Rabatt)
   → Bulk-Action: "Rechnungen freigeben" → state='posted'
   → Template wb_renewal_invoice versendet

3. Kunde zahlt bis zum Zahlungsziel
   → Payment-Hook:
   → sale.subscription verlängert (valid_to = Folgejahr 31.12.)
   → wb.license.key:
     - valid_to aktualisiert
     - Event 'renewal' geloggt
     - Neues Zertifikat generiert & versendet

4. Wenn Kunde NICHT zahlt bis invoice_date_due:
   → Odoo account_followup startet automatisch
   → Mahnstufe 1 wird erreicht (z.B. nach 7 Tagen)
     - Odoo sendet eigenes Mahnschreiben (nicht von uns!)
     - partner.followup_status = 'in_need_of_action'
     - Lizenz-Status bleibt 'active'
   → Mahnstufe 2 wird erreicht (z.B. nach 21 Tagen)
     - Wieder Odoo-Mahnung
     - Immer noch kein Grace
   → LETZTE Mahnstufe erreicht (z.B. nach 35 Tagen)
     - partner.followup_status = 'with_overdue_invoices'
     - Lizenz-Status: 'grace'
     - Template wb_grace_started (von uns)
     - Telegram an Tobias
     - Client zeigt Banner

5. Grace-Period läuft ab:
   → grace_until ist erreicht (= Datum der letzten Mahnstufe + Puffer)
   → Lizenz-Status: 'expired'
   → Template wb_license_expired
   → Client deaktiviert Lizenz-Features

6. Bei verspäteter Zahlung (auch nach 'expired'):
   → Tobias kann manuell reaktivieren
   → Event 'reactivated' geloggt
```

**Wichtig:** Die Mahn-Schreiben (Stufe 1, 2, 3) kommen aus Odoo-Standard
(`account_followup`). Unser System schickt nur zusätzliche Lizenz-
bezogene Nachrichten (Grace-Start, Expired).

### 9.4 Migration-Request

(unverändert zu v1.0 — keine Code-Prüfung, läuft über Tobias-Approval)

---

## 10. Sprint-Plan

Geringe Anpassung durch Activation-Code:

| # | Sprint | Inhalt | Aufwand | Reihenfolge |
|---|---|---|---|---|
| **1** | Core-Grundlage | Datenmodelle + Key+Code-Generator + bcrypt + UI | **7-9h** | Pflicht |
| **2** | Client-Modul | `wb_license_client` + Activation-Wizard + Ping | **5-7h** | Pflicht |
| **3** | Dokumente | Zertifikat + EULA + Activation-Mail-Template | 6-8h | Pflicht |
| **4** | Automations | Mails + Crons (Reminder, Renewal, Grace, Expiry) | **5-7h** | Pflicht |
| **5** | Trial-System | Trial-Formular + Endpoint (ohne Code-Logik) | 4-6h | Pflicht |
| **6** | Dashboard | Admin-Dashboard + KPIs + Telegram | 3-4h | Pflicht |
| **7** | Verifikation | Public Verify-Page + Captcha + Portal | 3-4h | Pflicht |
| **8** | Migration | Migration-Workflow + UI | 2-3h | Pflicht |
| **9** | ELSTER-Integration | `wb_elster_reports` + Client-Check | 2-3h | Pflicht |
| **10** | Polish | Design, Error-Messages, Docs, Release-Prep | 4-6h | Pflicht |

**Gesamt:** ~41-57h (v1.0 war 40-55h → +1-2h durch Activation-Code)

---

## 11. Offene Punkte & Risiken

### 11.1 Juristisch
- ⚠️ **EULA vom Anwalt prüfen lassen** (300-600 €)
- ⚠️ Impressum + Datenschutzerklärung auf wissen-beratung.de anpassen
- ⚠️ Cookie-Banner für reCAPTCHA
- ⚠️ AGB-Ergänzung für Abo-Produkte

### 11.2 Technisch
- ⚠️ Odoo App Store Review-Richtlinien einhalten
- ⚠️ **bcrypt als Python-Dependency** in Manifest aufnehmen
- ⚠️ **Rate-Limiting für Activation-Endpoint** implementieren (5 Fehlversuche/h)
- ⚠️ Backup-Strategie für License-DB (kritische Daten)
- ⚠️ **Code darf NIEMALS geloggt werden** — alle `_logger`-Aufrufe prüfen

### 11.3 Betrieb
- ⚠️ Support-Kanal definieren
- ⚠️ SLA-Commitment
- ⚠️ Stripe-Setup

### 11.4 Marketing
- ⚠️ Screenshots + Demo-Video
- ⚠️ Landing Page
- ⚠️ E-Mail-Sequenz für Trial

### 11.5 Security (neu v1.1)
- ⚠️ **Activation-Code niemals in Logs!** Explizite Audit-Tests
- ⚠️ **Rate-Limiting** zwingend (sonst Code-Brute-Force möglich)
- ⚠️ **bcrypt rounds=12** als Default (200ms = angenehm für User,
  zu langsam für Brute-Force)
- ⚠️ HTTPS zwingend (Code im Klartext über Draht sonst leakbar)

---

## 12. Changelog

| Version | Datum | Änderung |
|---|---|---|
| 1.0 | 23.04.2026 | Initiale Architektur |
| 1.1 | 23.04.2026 | Activation-Code (25 Zeichen) + bcrypt-Hash + "issued"-State hinzugefügt |
| 1.2 | 23.04.2026 | Code-Auslieferung via Portal-Ticket + Email-OTP (Kanal-Trennung). Key-Format: Checksum direkt an UUID angehängt (17 statt 19 Zeichen). |
| 1.3 | 23.04.2026 | Mahn-/Grace-Logik aus `account_followup` abgeleitet statt hart codiert. Zahlungsziel via `account.payment.term` pflegbar pro Kunde. Design: weißer Hintergrund durchgängig. |
| 1.4 | 23.04.2026 | Bestell-Flow: direkter Odoo-Controller statt n8n-Zwischenschritt. Weniger Komponenten, weniger Fehlerquellen. |
| 1.5 | 24.04.2026 | Review-Fixes vor Sprint 1: DECISION #48 (Fernet) fixiert, IP-Binding für Tickets, wb.license.info persistent, unknown-Gating entschärft (30d), Rate-Limit-Model, CORS differenziert. Strategie: Lizenz-Plattform vor erstem Produkt, ELSTER verworfen, TELE geplant. |
| **1.6** | **27.04.2026** | **License-Issuance vom Order-Confirm entkoppelt — fired jetzt erst bei `account.move.payment_state='paid'`. Order-Endpoint gibt keine Provider-URL zurück. Komplettes Stripe-Decoupling, folgt Odoo-Standards (DECISION #7).** |

---

## Anhang A — Entscheidungsprotokoll

**v1.0 Entscheidungen:** (siehe v1.0-Dokument)

**v1.1 Entscheidungen (23.04.2026):**
- Activation-Code als zweites Geheimnis eingeführt
- Format: 25 Zeichen Base32-reduziert (ohne 0,1,I,O), 5er-Gruppen
- Hash-Verfahren: bcrypt mit 12 Runden
- Einmal-Verwendung: Code wird nach Activation verbrannt
- Code-Gültigkeit: 90 Tage nach Erzeugung
- Activation-Code-Versand: separate E-Mail, NICHT auf Zertifikat/Rechnung
- Trial-Keys: keine Code-Pflicht (Friction-Reduction)
- Rate-Limiting: 5 Fehlversuche/h → Lock

**v1.2 Entscheidungen (23.04.2026):**
- **Security-Fix:** Activation-Code wird NICHT mehr per Email versendet
- Code-Auslieferung via Portal-Ticket + Email-OTP (Kanal-Trennung)
- Ticket-Format: `TCKT-{UUID22}`, 72h gültig
- Email-OTP: 6-stellig, 10 Min gültig, max. 5 Fehlversuche
- Code-Anzeige im Portal: 10 Min sichtbar, danach "verbrannt"
- Key-Format refactoring: Checksum direkt an UUID angehängt
  (`WB-ELST-a3f28c919K` statt `WB-ELST-a3f28c91-9K`)
- Begründung: Checksum ist Impl-Detail, nicht logisches Feld.
  Analog zu Luhn-Prüfziffer bei Kreditkarten / IBAN-Prüfziffer.

**v1.3 Entscheidungen (23.04.2026):**
- **Mahn-/Grace-Logik aus Odoo `account_followup` abgeleitet:**
  Grace-Period ist **nicht** mehr hart 14 Tage, sondern ergibt sich
  aus den konfigurierten Mahnstufen (`account.followup.line`).
- Zahlungsziele via Odoo-Standard `account.payment.term` pflegbar,
  pro Kunde individualisierbar
- Mahn-Emails kommen aus Odoo-Standard (nicht eigene Templates)
- Unser Modul schickt nur Lizenz-spezifische Nachrichten
  (Grace-Start, Expired) — der komplette Mahnungs-Vorgang läuft über
  das Odoo-Standard-Modul
- Design-Anpassung: Lizenzzertifikat bekommt weißen Hintergrund
  durchgängig (kein farbiger Header), wirkt dadurch seriöser

**v1.4 Entscheidungen (23.04.2026):**
- **Bestell-Flow direkt via Odoo-Controller:** Kein n8n als
  Zwischenschritt. Der Webseiten-Form POSTet direkt an
  `/api/wb_subscription/order` analog zum bestehenden Pattern von
  `/api/web/lead` in `wb_anpassungen`.
- Begründung: n8n wäre eine überflüssige Abstraktion gewesen —
  keine externen Systeme zu koordinieren, keine komplexe Branching-
  Logik, kein Upside. Weniger Komponenten = weniger Fehlerquellen,
  Debugging einfacher.

**v1.5 Entscheidungen (24.04.2026):**
- **DECISION #48 (Ticket-Code-Storage):** Activation-Code wird beim
  Ticket-Create mit Fernet (symmetric) verschlüsselt und in
  `wb.activation.ticket.encrypted_code` abgelegt. Nach `state='consumed'`
  wird `encrypted_code` gelöscht (Minimierung des Angriffsfensters).
  Fernet-Key aus ENV-Variable mit Fallback auf `ir.config_parameter`.
- **DECISION #48c (IP-Binding):** Portal-Ticket wird bei erstem GET
  an die IP gebunden. Nach 3 IP-Mismatches wird Ticket revoked.
  Schützt gegen versehentliches Email-Forwarding.
- **`wb.license.info` als `models.Model`:** Explizit persistent
  (nicht `TransientModel`). Sonst kein Cache über Odoo-Restarts.
- **`@license_required` 30-Tage-Toleranz bei `unknown`:** Früher 7.
  Gegen falsch-positive Sperrungen bei WB-Server-Ausfall. Kritische
  Methoden können per `min_cache_age_days`-Parameter strenger sein.
- **Rate-Limiting per `wb.rate.limit.entry`-Modell:** Eigenes Modell
  mit TTL-Einträgen und stündlichem Cleanup-Cron. Ersetzt das
  vage "ir-Table" aus v1.2.
- **CORS pro Endpoint-Typ differenziert:** `*` nur für Check/Activate/
  Migrate (Kunden-Odoos haben beliebige Domains). `*.wissen-beratung.de`
  für Trial/Order (nur eigene Webseite).
- **Strategie-Shift:** Lizenz-Plattform wird als produkt-agnostische
  Infrastruktur gebaut, bevor das erste reale Produkt existiert.
  Begründung: Tobias will vermeiden, das System später mehrfach
  umzubauen. Trade-off bewusst eingegangen.
- **Produkt-Status:** `ELST` verworfen (ELSTER-Steuermodul nicht
  mehr verfolgt), `wb_elster_reports/` bleibt als archiviertes Skelett.
  Erstes reales Produkt wird `TELE` (Telnyx-Integration) nach
  Abschluss Sprint 8.
- **Interims-Produkt-Code `TEST`:** Für End-to-End-Tests während
  Sprint 1–8 kann ein Dummy-Produkt mit Code `TEST` registriert
  werden, um den kompletten Aktivierungs-Flow zu validieren, ohne
  ein echtes Produkt zu brauchen.

---

*Ende des Dokuments.*
