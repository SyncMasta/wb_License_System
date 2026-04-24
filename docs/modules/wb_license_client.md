# wb_license_client — Modul-Briefing

**Status:** 🚧 Not yet built — Design-Doku als Grundlage für Implementation
**Zielversion:** `19.0.1.0.0`
**Läuft auf:** Kunden-Odoo-Instanz (als Dependency von Produkt-Modulen)
**Dependencies:** `base`, `mail`, `web`

---

## Zweck

Gemeinsames Basis-Modul für alle WISSEN BERATUNG Produkt-Module. Läuft
beim Kunden und kümmert sich um:

- **Lizenz-Aktivierung** (UI-Wizard zum Key+Code eingeben)
- **Täglicher Ping** an den WB-Lizenz-Server
- **Status-Caching** (offline-tolerant)
- **UI-Banner** (Ablauf, Grace, Expired)
- **Feature-Gates** (Decorator `@license_required('ELST')`)
- **Migration-Request-UI** (Kunde beantragt Umzug)

**Produkt-agnostisch:** Jedes WB-Produkt-Modul (ELSTER, DATEV, DSGVO) hängt
sich ein über `depends = ['wb_license_client']` und ruft:

```python
env['wb.license.client'].check_license('ELST')
```

um sicherzustellen, dass eine gültige Lizenz für das jeweilige Produkt
vorliegt.

---

## Module-Struktur

```
wb_license_client/
├── __manifest__.py
├── __init__.py
├── README.rst
├── models/
│   ├── __init__.py
│   ├── wb_license_client.py         # AbstractModel: Service-Klasse mit @api.model-Methoden
│   ├── wb_license_info.py           # models.Model: Persistenter Status-Cache (MUSS Model sein, nicht Transient!)
│   └── res_config_settings.py       # Einstellungen (Server-URL, optional Override)
├── controllers/
│   ├── __init__.py
│   └── main.py                      # interne Endpoints für Wizard-UI
├── wizards/
│   ├── __init__.py
│   ├── wb_license_activate_wizard.py
│   └── wb_license_migrate_wizard.py
├── data/
│   ├── ir_cron_data.xml             # Täglicher Ping-Cron
│   └── mail_template_data.xml       # Lokale Warn-Emails (an Admin)
├── views/
│   ├── wb_license_activate_wizard_views.xml
│   ├── wb_license_migrate_wizard_views.xml
│   ├── wb_license_info_views.xml
│   ├── res_config_settings_views.xml
│   └── assets.xml                   # JS für Banner
├── security/
│   └── ir.model.access.csv
├── static/
│   ├── description/
│   │   └── icon.png
│   └── src/
│       ├── js/
│       │   └── license_banner.js    # Top-Banner wenn Lizenz-Issue
│       └── scss/
│           └── license_banner.scss
└── tests/
    ├── __init__.py
    ├── test_activation_wizard.py
    ├── test_ping_cycle.py
    └── test_feature_gate.py
```

---

## Modelle im Detail

### 1. `wb.license.client` — Service-Klasse (AbstractModel)

Der Haupt-Service. Produkt-Module rufen ihn auf:

```python
# In einem Produkt-Modul (z.B. wb_elster_reports):
class WbElsterUstva(models.Model):
    _inherit = 'wb.elster.ustva'

    def action_export_xml(self):
        # Lizenz-Check vor kritischer Operation
        license_info = self.env['wb.license.client'].check_license('ELST')
        if not license_info.is_valid:
            raise UserError(license_info.user_message)
        # ... normal weiter
```

**Methoden:**

```python
class WbLicenseClient(models.AbstractModel):
    _name = 'wb.license.client'
    _description = 'WB License Client Service'

    @api.model
    def check_license(self, product_code):
        """Hauptmethode. Liefert WbLicenseInfo mit Status.

        Flow:
          1. Key aus ir.config_parameter lesen
          2. Cached Status prüfen (wb.license.info, TTL 24h)
          3. Wenn frisch: zurückgeben
          4. Wenn alt: Ping an Server, Cache aktualisieren
          5. Wenn Server offline: alten Cache-Eintrag zurückgeben
             (Grace-Window: bis zu 7 Tage tolerieren)
          6. Wenn auch das fehlt: 'unknown' Status

        :param product_code: 'ELST', 'DATV', ...
        :return: wb.license.info Record
        """

    @api.model
    def activate_license(self, product_code, key, activation_code):
        """Wird vom Activate-Wizard aufgerufen.
        Ruft /api/license/activate auf Server.
        Speichert Key bei Erfolg in ir.config_parameter."""

    @api.model
    def request_migration(self, product_code, new_domain, new_db_uuid, reason):
        """Wird vom Migrate-Wizard aufgerufen.
        Ruft /api/license/migrate auf Server."""

    @api.model
    def _do_ping(self, product_code, key):
        """Internal: eigentlicher HTTP-Call an /api/license/check."""

    @api.model
    def _get_stored_key(self, product_code):
        """Liest Key aus ir.config_parameter.
        Key: 'wb_license_client.key_ELST', 'wb_license_client.key_DATV' etc."""

    @api.model
    def _store_key(self, product_code, key):
        """Speichert Key in ir.config_parameter."""

    @api.model
    def _get_server_url(self):
        """Server-URL aus ir.config_parameter,
        Default: 'https://wissen-beratung.de'."""

    @api.model
    def _get_db_uuid(self):
        """Liest ir.config_parameter 'database.uuid'."""

    @api.model
    def _get_domain(self):
        """Liest ir.config_parameter 'web.base.url'."""
```

**Decorator für Feature-Gates:**

```python
from functools import wraps

def license_required(product_code, min_cache_age_days=30):
    """Decorator für Methoden die eine gültige Lizenz erfordern.

    Args:
        product_code: 4-stelliger Produkt-Code (z.B. 'TELE')
        min_cache_age_days: Maximales Alter der letzten erfolgreichen Server-Prüfung
            für 'unknown'-State. Default 30 (liberal, für Offline-Toleranz).
            Setze auf 7 oder weniger für Methoden die externe Kosten verursachen.

    Usage:
        @license_required('TELE')
        def action_send_sms(self):
            ...

        @license_required('TELE', min_cache_age_days=7)
        def action_send_bulk_sms(self):  # verursacht reale Kosten bei Telnyx
            ...
    """
    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            info = self.env['wb.license.client'].check_license(
                product_code, min_cache_age_days=min_cache_age_days)
            if not info.is_valid:
                from odoo.exceptions import UserError
                raise UserError(info.user_message)
            return func(self, *args, **kwargs)
        return wrapper
    return decorator
```

### 2. `wb.license.info` — Status-Cache (persistent)

**Wichtig:** Dies ist ein **`models.Model`** (persistent), **kein** `TransientModel`.
Der Cache muss Odoo-Restarts und Server-Ausfälle überleben, sonst
fällt die Lizenz bei jedem Neustart in `state='unknown'` und Kunden
bekommen unnötig Warn-Banner.

| Feld | Typ | Beschreibung |
|---|---|---|
| `product_code` | Char(4) | 'TELE', 'DATV', ... — unique zusammen mit company_id |
| `key` | Char | Public Key (in config_parameter gespiegelt) |
| `state` | Selection | 'active', 'grace', 'expired', 'unknown', 'unlicensed' |
| `valid_from` | Date | aus Server-Response |
| `valid_to` | Date | aus Server-Response |
| `grace_until` | Date | aus Server-Response |
| `days_remaining` | Integer computed | |
| `last_server_check` | Datetime | Letzter erfolgreicher Ping |
| `last_check_success` | Boolean | |
| `last_error_message` | Char | |
| `user_message` | Text computed | Benutzerfreundliche Fehlermeldung |
| `is_valid` | Boolean computed | siehe Logik unten |
| `server_response_raw` | Text | Original-JSON-Response (für Debugging) |
| `company_id` | M2O res.company | Für Multi-Company-Setups |

**SQL-Constraint:** `UNIQUE(product_code, company_id)` — genau ein Cache-Eintrag pro Produkt pro Company.

**States:**
- `active` — Lizenz gültig, alles OK
- `grace` — Rechnung überfällig, Warnung im Banner
- `expired` — Lizenz abgelaufen, Features gesperrt
- `unknown` — Server unerreichbar (zeigt zuletzt bekannten Status)
- `unlicensed` — Kein Key konfiguriert (Modul wird nicht genutzt oder frisch installiert)

**user_message-Logik (computed):**

```python
def _compute_user_message(self):
    messages = {
        'active': False,
        'grace': _(
            "Ihre Lizenz läuft aus wegen einer überfälligen Rechnung. "
            "Bitte bezahlen Sie die offene Rechnung bis zum %s, um "
            "Unterbrechungen zu vermeiden."
        ) % self.grace_until,
        'expired': _(
            "Ihre Lizenz ist abgelaufen. Bitte kontaktieren Sie "
            "support@wissen-beratung.de für eine Verlängerung."
        ),
        'unknown': _(
            "Der Lizenz-Server ist nicht erreichbar. Ihre Lizenz "
            "funktioniert weiter, aber bitte prüfen Sie Ihre "
            "Internet-Verbindung. Bei Problemen: support@wissen-beratung.de"
        ),
        'unlicensed': _(
            "Keine Lizenz konfiguriert. Bitte unter "
            "Einstellungen → WISSEN BERATUNG Lizenzen aktivieren."
        ),
    }
    for rec in self:
        rec.user_message = messages.get(rec.state, False)
```

**is_valid-Logik (v1.5 — entschärft):**
- `active` → True
- `grace` → True (Features funktionieren noch, nur Banner warnt)
- `expired` → **False** (Features gesperrt)
- `unknown` → **True wenn letzte erfolgreiche Prüfung < 30 Tage her**, sonst False
- `unlicensed` → False

**Begründung für 30-Tage-Toleranz bei `unknown`:**
Der frühere 7-Tage-Wert wurde auf 30 erhöht. Motivation: Wenn der WB-Lizenz-Server ausfällt (Hardware, DNS, Provider-Outage), dürfen zahlende Kunden nicht sperr-blockiert werden. Der Schmerz eines fälschlich gesperrten Zahlers ist größer als der Vorteil, einen Piracy-Versuch 23 Tage früher zu erkennen — Piraterie-Schutz läuft ohnehin primär über Activation + bound_domain, nicht über Ping-Timeouts.

**Operations-Alerts bei `unknown`:**
Ab Tag 3 `unknown` → lokale Admin-Mail an `company.email` (Template `mail_template_license_server_unreachable`). Ab Tag 14 → zusätzlich rote Banner-Meldung im Backend. Ab Tag 30 → `is_valid=False`, Features gesperrt.

**Ausnahme für kritische Methoden:**
Produkt-Module können pro Methode eine strengere Policy verlangen:
```python
@license_required('TELE', min_cache_age_days=7)  # nur wenn Cache < 7 Tage alt
def action_expensive_telnyx_call(self):
    ...
```
Default ist `min_cache_age_days=30`. Dieser Parameter wird nur gesetzt für Methoden, die real Kosten beim externen Provider verursachen (z.B. kostenpflichtige SMS-Sends) — dort ist Piracy-Schutz wichtiger als Offline-Toleranz.

### 3. `res.config.settings` — Lokale Einstellungen

```python
wb_license_server_url = fields.Char(
    string='Lizenz-Server-URL',
    default='https://wissen-beratung.de',
    help='Normalerweise nicht ändern. Nur für Test-/Staging-Umgebungen.',
)
wb_license_debug_mode = fields.Boolean(
    string='Debug-Modus',
    help='Zeigt Server-Responses als Notification. Nicht in Produktion!',
)
```

Im Admin-UI wird pro installiertem Produkt-Modul angezeigt:
- Produkt-Name
- Lizenz-Status (Badge)
- Key (maskiert: `WB-ELST-...-9K`)
- Laufzeit
- Button "Lizenz aktivieren" (wenn unlicensed)
- Button "Details" (öffnet Dashboard)

---

## Wizards

### Activate-Wizard

**Model:** `wb.license.activate.wizard` (TransientModel)

**Felder:**

| Feld | Typ | Required |
|---|---|---|
| `product_code` | Selection (aus installierten Produkten) | Yes |
| `key` | Char | Yes |
| `activation_code` | Char | Yes |
| `info_message` | Html readonly | computed |

**Validation beim Eintippen (client-side):**
- Format-Check: `WB-[A-Z]{4}-[a-f0-9]{8}[A-Z2-7]{2}` für Key
- Format-Check: `[A-HJ-NP-Z2-9]{5}(-[A-HJ-NP-Z2-9]{5}){4}` für Code
- Bei Fehler: rotes Feld mit Tooltip

**Workflow:**

```
User: klickt "Lizenz aktivieren" in Settings
  │
  ▼
Wizard öffnet sich
  │
  ├─ Feld: Produkt (Dropdown mit installierten Produkten)
  ├─ Feld: Lizenzschlüssel (mit Format-Hint)
  └─ Feld: Aktivierungs-Code (mit Format-Hint)
  │
  │ User klickt "Aktivieren"
  ▼
Client-side Format-Check → Error wenn falsch
  │
  ▼
HTTP POST /api/license/activate
  │
  ├─ Success (200):
  │   → Key in ir.config_parameter speichern
  │   → Cache-Eintrag wb.license.info updaten
  │   → Wizard mit Success-Message schließen
  │   → User-Notification: "✅ Lizenz aktiviert"
  │
  ├─ 401 (Wrong Code):
  │   → Error im Wizard: "Code falsch, bitte prüfen"
  │
  ├─ 409 (Already Activated):
  │   → Error: "Dieser Key ist bereits an anderer Instanz aktiv.
  │             Migration beantragen?"
  │
  ├─ 410 (Expired):
  │   → Error: "Aktivierungs-Zeitraum abgelaufen. Bitte Support kontaktieren."
  │
  └─ 5xx / Network Error:
      → Error: "Server nicht erreichbar. Bitte später erneut versuchen."
```

**Wizard-View:**

```xml
<form string="Lizenz aktivieren">
    <sheet>
        <div class="alert alert-info">
            <p>Bitte geben Sie Ihren Lizenzschlüssel und Aktivierungs-Code ein.</p>
            <p>Der Aktivierungs-Code wird Ihnen nach Klick auf den Link
               in der Aktivierungs-Email im geschützten Portal angezeigt.</p>
        </div>

        <group>
            <field name="product_code"/>
            <field name="key" placeholder="WB-XXXX-xxxxxxxxXX"/>
            <field name="activation_code" placeholder="XXXXX-XXXXX-XXXXX-XXXXX-XXXXX"/>
        </group>

        <field name="info_message" nolabel="1"/>
    </sheet>
    <footer>
        <button name="action_activate" type="object" string="Aktivieren" class="btn-primary"/>
        <button special="cancel" string="Abbrechen"/>
    </footer>
</form>
```

### Migrate-Wizard

**Model:** `wb.license.migrate.wizard` (TransientModel)

**Felder:**

| Feld | Typ | Required |
|---|---|---|
| `product_code` | Selection | Yes |
| `current_domain` | Char readonly | - (aus Config) |
| `current_db_uuid` | Char readonly | - |
| `new_domain` | Char | Yes |
| `new_db_uuid` | Char readonly | - (= aktuelle DB-UUID der neuen Instanz) |
| `reason` | Text | Yes |
| `contact_email` | Char | Yes |

**Anwendungsfall:** Der Wizard wird **auf der NEUEN Instanz** geöffnet
(wo die Lizenz hin soll). Er liest dort die aktuelle DB-UUID und Domain
und sendet sie an den Server.

**Flow:**

```
User (in neuer Odoo-Instanz): öffnet Wizard
  │
  │ Gibt Key ein (muss er schon wissen)
  │
  │ Wizard liest automatisch:
  │   - current_domain = ir.config_parameter 'web.base.url'
  │   - current_db_uuid = ir.config_parameter 'database.uuid'
  │
  │ Klickt "Migration beantragen"
  │
  ▼
POST /api/license/migrate
  │
  ▼
Server erstellt wb.license.migration.request (state='pending')
  → Telegram + Email an Tobias
  → Email an Kunde: "Bestätigung eingegangen, Bearbeitung 1-2 Werktage"

Tobias approved oder rejected im Backend

Bei Approval:
  → Key bei Tobias: bound_domain + bound_db_uuid werden auf neue Werte gesetzt
  → Neues Zertifikat generiert
  → Email an Kunde mit neuem Zertifikat
  → Beim nächsten Ping von der NEUEN Instanz: Status 'active'
```

---

## Cron: Täglicher Ping

**Name:** `WB: License Daily Ping`
**Rhythmus:** Täglich, zufällig zwischen 01:00 und 05:00 UTC (Load-Balancing für Server)
**Methode:** `wb.license.client._cron_daily_ping()`

```python
@api.model
def _cron_daily_ping(self):
    """Pingt alle konfigurierten Lizenzen an den Server.

    Pro Produkt-Modul:
      1. Key aus config_parameter lesen
      2. POST /api/license/check
      3. wb.license.info aktualisieren
      4. State-Change → ggf. Admin-Mail (an company_id.email)
    """
    # Alle Produkt-Codes finden aus installierten Modulen
    # (über IrModuleModule mit Prefix 'wb_')

    for product_code in self._get_all_installed_product_codes():
        try:
            self._do_ping(product_code)
        except Exception as e:
            _logger.exception("[wb_license_client] Ping for %s failed: %s",
                              product_code, e)
            # Nicht abbrechen — nächstes Produkt versuchen
```

**Rausomisierung der Ping-Zeit:**
Cron-Record hat `nextcall` initial auf 01:00, nach jedem Ping wird `nextcall`
auf `heute + 1 Tag + random(0..4h)` gesetzt. So verteilen sich alle Kunden-
Pings auf 4 Stunden statt alle um Punkt 01:00 den Server zu überrennen.

---

## UI-Banner (JS Frontend)

### Konzept

Auf allen Backend-Seiten erscheint ein Banner **oben über der Top-Navigation**
wenn die Lizenz in Problem-State ist:

- `grace`: Gelb, "⚠️ Lizenz läuft aus — Rechnung offen. Details"
- `expired`: Rot, "🔴 Lizenz abgelaufen. Features gesperrt. Jetzt verlängern"
- `unknown` (>3 Tage): Grau, "⚠️ Lizenz-Server unerreichbar"

Banner ist **dismissible** aber kommt nach 24h wieder.

### Implementation

**Datei:** `static/src/js/license_banner.js`

```javascript
/** @odoo-module **/
import { registry } from "@web/core/registry";

const licenseService = {
    dependencies: ["rpc", "notification"],

    async start(env, { rpc, notification }) {
        // Beim Start: Lizenzen abfragen
        const infos = await rpc("/wb_license_client/get_all_infos", {});

        for (const info of infos) {
            if (info.state === 'expired' || info.state === 'grace') {
                this.showBanner(info);
            }
        }
    },

    showBanner(info) {
        // DOM-Injection eines Banners über der Top-Nav
        // Dismiss-Button setzt Cookie mit 24h TTL
    },
};

registry.category("services").add("wb_license_banner", licenseService);
```

**Controller (für RPC):**

```python
@http.route('/wb_license_client/get_all_infos', type='json', auth='user')
def get_all_license_infos(self):
    infos = request.env['wb.license.info'].search([])
    return [info.read()[0] for info in infos]
```

---

## Mail-Templates (lokal)

Nur **zwei** Templates — für Kommunikation an den lokalen Admin (nicht an WB):

| Template | Trigger | Empfänger |
|---|---|---|
| `mail_template_license_grace_admin` | State wechselt zu 'grace' | company.email |
| `mail_template_license_expired_admin` | State wechselt zu 'expired' | company.email |

Die Haupt-Kommunikation (Renewal-Mahnungen, etc.) läuft über WB-Server
direkt an den Kunden — dieses Modul sendet nur bei kritischen lokalen
Zuständen.

---

## Security

### Gruppen

**Keine neuen Gruppen.** Nur Odoo-Standard:
- `base.group_user` — darf `wb.license.info` lesen (read-only)
- `base.group_system` — darf Activate-Wizard nutzen

### ACL

```csv
access_wb_license_info_all,wb.license.info all,model_wb_license_info,base.group_user,1,0,0,0
access_wb_license_info_admin,wb.license.info admin,model_wb_license_info,base.group_system,1,1,1,1
access_wb_license_activate_wizard,activate wizard,model_wb_license_activate_wizard,base.group_system,1,1,1,1
access_wb_license_migrate_wizard,migrate wizard,model_wb_license_migrate_wizard,base.group_system,1,1,1,1
```

---

## API-Calls an den WB-Server

Alle Calls via `requests` Library.

### Einheitliche Header

```python
def _get_headers(self):
    return {
        'Content-Type': 'application/json',
        'User-Agent': f'wb_license_client/{self.module_version}',
        'X-WB-DB-UUID': self._get_db_uuid(),
        'X-WB-Domain': self._get_domain(),
    }
```

### Timeout-Handling

```python
def _do_request(self, endpoint, payload, timeout=10):
    """Unified request with retry logic."""
    url = self._get_server_url() + endpoint
    headers = self._get_headers()

    try:
        response = requests.post(url, json=payload,
                                  headers=headers, timeout=timeout)
        return response.json(), response.status_code
    except requests.Timeout:
        _logger.warning("[wb_license_client] Timeout at %s", endpoint)
        return None, 0  # 0 = network error
    except requests.ConnectionError:
        _logger.warning("[wb_license_client] Connection error at %s", endpoint)
        return None, 0
    except Exception as e:
        _logger.exception("[wb_license_client] Unexpected error at %s: %s",
                          endpoint, e)
        return None, 0
```

### Retry-Logik

**Beim `/api/license/check`:**
- Timeout 10s
- Bei Network-Error: 1x Retry nach 30s
- Bei 5xx: kein Retry (Server-Problem, nicht unseres)

**Beim `/api/license/activate`:**
- Timeout 15s
- Bei Network-Error: **kein** Retry (User soll nochmal klicken)

---

## Tests

### `test_activation_wizard.py`

- Format-Validation (Key/Code falsch → Error sichtbar)
- Erfolgreiche Activation → Key in config_parameter
- Wrong Code → Error-Message korrekt
- Network Error → User-freundliche Fehlermeldung

### `test_ping_cycle.py`

- First Ping → Cache-Eintrag erstellt
- Subsequent Ping (<24h) → nutzt Cache
- Subsequent Ping (>24h) → HTTP-Call
- Server Offline → letzter Cache bleibt, state='unknown' nach 3 Tagen
- State-Change (active → grace) → Admin-Mail gesendet

### `test_feature_gate.py`

- `@license_required('ELST')` mit gültiger Lizenz → Methode läuft
- Mit expired Lizenz → `UserError` geworfen
- Mit unlicensed Status → `UserError` mit passendem Link
- Mit `grace` → Methode läuft (nicht blockiert, nur gewarnt)

---

## Acceptance-Kriterien

Sprint 2 ist fertig, wenn:

- [ ] `wb.license.client` kann Keys aktivieren (Wizard)
- [ ] Täglicher Ping läuft (Cron)
- [ ] Cache funktioniert offline-tolerant
- [ ] Feature-Gate-Decorator sperrt bei expired
- [ ] Banner erscheint bei grace/expired (Frontend-JS)
- [ ] Migration-Wizard sendet Request an Server
- [ ] Alle Tests grün
- [ ] README.rst mit Install + Activation-Anleitung

---

## Offene Punkte (vor Sprint 2 zu entscheiden)

1. **Modul-Aktivierung initial:** Soll beim `wb_license_client` Install automatisch ein Setup-Wizard aufpoppen? (Empfehlung: ja)
2. **Multi-Company:** Ein Key pro Company oder ein Key pro Odoo-Instanz? (Empfehlung: pro Instanz, Multi-Company wird in Key-Staffelung abgebildet)
3. **Test-Modus:** Braucht das Modul einen offiziellen Test-Key (z.B. `WB-TEST-xxx`)? (Empfehlung: nein, für Testing lieber Staging-Server mit echten Keys)

---

## Integration mit Produkt-Modulen

### Beispiel: wb_elster_reports

**Manifest:**
```python
{
    'depends': [
        'base',
        'account',
        'wb_license_client',  # ← Pflicht für alle verkäuflichen Module
    ],
    ...
}
```

**Nutzung im Code:**

```python
# Option A: Decorator (bevorzugt)
from odoo.addons.wb_license_client.models.wb_license_client import license_required

class WbElsterUstva(models.Model):
    _inherit = 'wb.elster.ustva'

    @license_required('ELST')
    def action_export_xml(self):
        # ... bestehende Logik
        pass


# Option B: Imperativer Check
def action_export_xml(self):
    info = self.env['wb.license.client'].check_license('ELST')
    if not info.is_valid:
        raise UserError(info.user_message)
    # ...
```

### Welche Methoden müssen gated sein?

Nur **feature-kritische** Operationen, nicht jede Read-Operation:

- ✅ XML-Export, PDF-Generation, API-Calls → gated
- ✅ Neue Records anlegen → gated
- ❌ Listen anschauen, Records lesen → **nicht** gated (Kunde soll seine historischen Daten behalten)
- ❌ Settings öffnen → **nicht** gated

**Prinzip:** Nach Ablauf soll Kunde **read-only** auf seine Daten zugreifen
können, aber keine neuen Reports mehr erzeugen. So hat er Anreiz zu
verlängern, aber keinen Grund dich zu verklagen.

---

## Abschätzung Aufwand

| Bereich | Stunden |
|---|---|
| Modelle + Cache-Logik | 2 |
| HTTP-Client + Retry | 1 |
| Activate-Wizard + Validation | 2 |
| Migrate-Wizard | 1 |
| Ping-Cron | 1 |
| UI-Banner (JS) | 2 |
| Feature-Gate-Decorator | 1 |
| Tests | 3 |
| Integration in wb_elster_reports | 1 |
| Dokumentation | 1 |
| **Total** | **~15h** |

Realistisch: 2-3 Abende à 6h.
