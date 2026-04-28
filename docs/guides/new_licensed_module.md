# Primer: Neues lizenziertes WB-Produkt-Modul bauen

> **Wofür ist diese Datei?**
> Lade sie als Kontext bevor Du ein neues verkäufliches WB-Modul anfängst
> (z.B. `wb_telnyx`, `wb_datev_export`). Sie enthält alles was Du brauchst,
> um sauber an `wb_subscription` + `wb_license_client` anzuknüpfen — ohne
> in zehn anderen Dateien herumzuwühlen.
>
> Sprache: Deutsch. Stil: pragmatisch, anti-fluff.

---

## 0. TL;DR — Was hängt am Lizenz-System

Jedes verkäufliche WB-Modul muss:

1. **`wb_license_client` als `depends`** im Manifest haben.
2. Einen **4-Char Produkt-Code** registrieren (`product.product` mit
   `wb_is_license_product=True` + `wb_technical_code='XXXX'`).
3. **`_post_init_hook`** anbieten der `register_install('XXXX')` ruft (Lead-Registry).
4. Feature-kritische Methoden mit **`@license_required('XXXX')`** dekorieren.
5. Sich an die WB-Konventionen halten (Naming, Branding, Style).

Der Kunde installiert das Modul → Lead-Eintrag entsteht serverseitig →
Tobias sieht den Lead in „WB Lizenzen → Installierte Addons" → bei Kauf
+ Activate wandert der Eintrag automatisch in `state='converted'`.

---

## 1. Produkt-Code reservieren

Eindeutige 4 Großbuchstaben. Vor Implementierungsstart in
`DECISIONS.md` Abschnitt „Produkt-Code-Registry" eintragen.

**Aktuell vergeben (Stand 2026-04-27):**

| Code | Modul |
|---|---|
| TEST | Test-/Dev-Produkt |
| TELE | Telnyx Voice + SMS |
| DATV | DATEV Export (Konzept) |
| DSGV | DSGVO Auskunft (Konzept) |
| ELST | (verworfen) |

Constraint im Code: `^[A-Z]{4}$`, UNIQUE auf `product.template.wb_technical_code`.

---

## 2. Modul-Skelett

```
wb_<modul>/
├── __manifest__.py             post_init_hook='_wb_<modul>_post_init'
├── __init__.py                 mit register_install-Hook
├── README.rst                  Pflicht für verkäufliche Module
├── models/
│   ├── __init__.py
│   └── *.py                    _name = 'wb.<domain>.<thing>'
├── views/
│   └── *.xml                   IDs: wb_<modul>_<model>_<typ>
├── data/                       Sequenzen, Mail-Templates
├── security/
│   ├── wb_<modul>_security.xml  Gruppen
│   └── ir.model.access.csv
├── static/description/
│   ├── icon.png                128x128, Navy-Hintergrund #1D3C6E
│   └── index.html              Marketplace-Beschreibung
├── tests/
│   └── test_*.py               TransactionCase + @tagged('wb_<modul>')
└── doc/                        Nutzer-Doku
```

### `__manifest__.py` Template

```python
{
    'name': 'WB <Produktname>',
    'version': '19.0.1.0.0',
    'category': '<Sales | Accounting | Productivity ...>',
    'summary': '<Kurzer Pain-before-solution-Satz>',
    'description': """...""",
    'author': 'WISSEN BERATUNG (Tobias Wissen)',
    'website': 'https://www.wissen-beratung.de',
    'license': 'OPL-1',
    'depends': [
        'base',
        'mail',
        'wb_license_client',          # ← Pflicht
        # ... domänen-spezifisch
    ],
    'external_dependencies': {
        'python': [],                  # nur was ihr wirklich braucht
    },
    'data': [
        'security/wb_<modul>_security.xml',
        'security/ir.model.access.csv',
        'data/...xml',
        'views/...xml',
    ],
    'assets': {},
    'installable': True,
    'application': False,              # True wenn Top-Level-Menü
    'auto_install': False,
    'post_init_hook': '_wb_<modul>_post_init',
}
```

### `__init__.py` Template

```python
import logging

from . import models
# from . import controllers     # falls HTTP-Endpoints

_logger = logging.getLogger(__name__)


def _wb_<modul>_post_init(env):
    """Meldet diesen Install bei wissen-beratung.de an (Lead-Registry).

    Best-effort — ein Server-Ausfall darf den Module-Install nicht
    crashen. Opt-out via ir.config_parameter
    'wb_license_client.disable_install_registry'.
    """
    try:
        env['wb.license.client'].register_install('XXXX')   # 4-Char Code
    except Exception as e:
        _logger.warning("[wb_<modul>] register_install failed: %s", e)
```

---

## 3. Feature-Gates: `@license_required`

Importiere den Decorator und gate **nur feature-kritische** Methoden:

```python
from odoo.addons.wb_license_client.models.wb_license_client import license_required


class WbTelnyxOutbound(models.Model):
    _inherit = 'wb.telnyx.outbound'

    @license_required('TELE')
    def action_send_sms(self):
        # ... Logik
        ...

    @license_required('TELE', min_cache_age_days=7)
    def action_send_bulk_sms(self):
        # Externe Telnyx-Kosten → strenger als Default 30
        ...
```

### Was gaten / was nicht

| Gaten? | Operation |
|---|---|
| ✅ | XML/PDF-Export, API-Calls zu externen Providern, neue Records anlegen |
| ✅ | Alles was reale Kosten verursacht |
| ❌ | Listen anschauen, Records lesen — Kunde muss seine historischen Daten behalten |
| ❌ | Settings öffnen |
| ❌ | Cron-Jobs die Backend-Daten bereinigen |

**Prinzip:** Nach Lizenz-Ablauf → read-only auf eigene Daten, keine
neuen Operationen. Kunde hat Anreiz zu verlängern, keinen Grund zu
verklagen.

### `min_cache_age_days` — Strategie

| Methodentyp | Wert | Begründung |
|---|---|---|
| Default | `30` | 30 Tage Server-Down-Toleranz |
| Externe Kosten (Telnyx, Brevo, …) | `7` | Piracy-Schutz wichtiger |
| Interne CPU-Operationen | `30` (default) | Keine Drittkosten, keine Dringlichkeit |

---

## 4. Lizenz-States — was bedeutet was

Vom Server gelieferte States in `wb.license.info.state`:

| State | `is_valid`* | Bedeutung |
|---|---|---|
| `active` | True | Alles gut |
| `grace` | True | Rechnung überfällig — Banner mahnt, Features laufen |
| `expired` | False | Lizenz abgelaufen — Features gesperrt |
| `unknown` | True¹ | Server unerreichbar — letzten Stand zeigen |
| `unlicensed` | False | Kein Key konfiguriert |

¹ `unknown` ist nur valid solange `last_server_check < min_cache_age_days` her. Danach `is_valid=False`.

* `is_valid=True` → Decorator lässt Methode laufen.

---

## 5. Server-Seite: Was Tobias machen muss

Damit das Produkt verkaufbar ist, braucht es ein `product.product`
mit Lizenz-Konfiguration:

| Feld | Wert |
|---|---|
| `wb_is_license_product` | `True` |
| `wb_technical_code` | 4 Großbuchstaben (matched mit Code im Decorator) |
| `wb_module_technical_name` | `wb_<modul>` (für Auslieferung) |
| `wb_instance_limit` | i.d.R. `1` |
| `wb_activation_grace_days` | i.d.R. `90` |
| `wb_trial_days` | i.d.R. `7` |
| `list_price` | Listenpreis |
| `recurring_invoice` | True (für Subscriptions) |

**Auto-Issuance bei Kauf:** Sobald `account.move.payment_state='paid'`,
ruft der Hook `sale_order._wb_issue_license_keys()` auf, das automatisch
`wb.license.key` + `wb.activation.ticket` anlegt und Mails versendet.
Kein Custom-Code dafür im Produkt-Modul nötig.

---

## 6. Tests

Mindestens ein TransactionCase pro Produkt-Modul. Setup-Pattern siehe
`wb_subscription/tests/test_install_registry.py` als Referenz.

```python
import os
from cryptography.fernet import Fernet
from odoo.tests import TransactionCase, tagged


@tagged('wb_<modul>')
class TestWb<Modul>Feature(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._fernet_env_backup = os.environ.get('WB_SUBSCRIPTION_FERNET_KEY')
        os.environ['WB_SUBSCRIPTION_FERNET_KEY'] = Fernet.generate_key().decode('utf-8')

        cls.product_template = cls.env['product.template'].create({
            'name': 'WB <Modul>',
            'wb_is_license_product': True,
            'wb_technical_code': 'XXXX',
        })

    def test_gated_method_raises_without_license(self):
        # Kunde ohne Key → UserError erwartet
        ...
```

Lauf-Befehl auf s02:
```bash
sudo -u odoo19 /opt/odoo19/venv/bin/python3.12 /opt/odoo19/odoo-bin \
  -c /etc/odoo19.conf -d Main --test-tags wb_<modul> --stop-after-init
```

---

## 7. Naming-Conventions (verbindlich)

| Element | Pattern | Beispiel |
|---|---|---|
| Modulname | `wb_<modul>` | `wb_telnyx` |
| Model | `wb.<domain>.<thing>` | `wb.telnyx.outbound` |
| Feld auf Standard-Models | `wb_<feld>` | `res.partner.wb_license_ids` |
| XML-ID | `wb_<modul>_<obj>_<typ>` | `wb_telnyx_outbound_view_form` |
| Logger-Prefix | `[wb_<modul>]` | `_logger.info("[wb_telnyx] ...")` |
| Security-Gruppen | `group_wb_<modul>_user` / `_manager` | |
| Cron-XML-ID | `ir_cron_<modul>_<task>` | |

Niemals `wissen_beratung_<modul>` — zu lang. Niemals camelCase in
XML-IDs.

---

## 8. Code-Style

- **Python 3.12-Syntax**: f-strings, type hints wo's hilft, kein `format()`
- **Docstrings:** Englisch (Klassen + Methoden)
- **Comments:** Deutsch OK für Domänen-Logik (Steuern, Buchhaltung, Kunden-UX)
- **Kein** `try/except: pass` — entweder Exception nach oben oder
  `_logger.exception` mit Kontext
- **`message_post`/`message_notify`**: Body als `Markup(...)` aus
  `markupsafe`, sonst wird HTML escaped
- **Author bei Mails:** `env.ref('base.partner_root').id` (OdooBot),
  niemals `self.env.user`
- **Keine Odoo-Studio-Anpassungen** — alles via Code, updatefest

---

## 9. Branding-Assets

- **Farben:** Navy `#1D3C6E`, Blue `#1A8BC4`, Cyan `#00C8E8`
- **Icon:** 128×128, Navy-Hintergrund, weißes/cyan Symbol
- **Logo:** Auf Zertifikaten, EULAs, Rechnungen
- **Tonalität (Marke):** Pain-before-solution, keine hohlen Phrasen,
  Beweise vor Versprechen
- **Tonalität (Tobias persönlich):** Norddeutsch-pragmatisch,
  „so geht das" statt „we empower you to"

---

## 10. Odoo-19-Stolpersteine (vermeidbar)

- `res.groups.users` → **`group.user_ids`** (oder besser: search auf res.users mit `('group_ids', 'in', g.id)`)
- `message_notify(record_name=…)` und `email_layout_xmlid` → **wirft Error**, nutze stattdessen `message_post(partner_ids=…)`
- `ir.cron.numbercall` und `doall` **nicht mehr** vorhanden
- `res.groups.category_id` **entfernt** → `res.groups.privilege` (oder weglassen)
- View-Inherits: stabile Anker wie `<app name="…">`, **nicht** Block-IDs
- `relativedelta` und `context_today` in **search-domains** → wirft Error in 19, vor-rechnen oder im Code-Filter
- Author = Empfänger → Discuss filtert die Nachricht raus → immer OdooBot als author

---

## 11. Deployment-Workflow (s02)

```bash
# Lokal validieren
python3 -m py_compile $(find wb_<modul> -name '*.py')
python3 -c "import xml.etree.ElementTree as ET; \
  [ET.parse(f) for f in $(find wb_<modul> -name '*.xml')]"

# Bauen + hochladen
tar -czf /tmp/wb_<modul>.tar.gz wb_<modul>/
scp /tmp/wb_<modul>.tar.gz root@s02:/tmp/

# Auf s02 deployen (mit Stop, sonst „Address already in use")
ssh root@s02 '
  cd /opt/odoo19/custom_addons/
  rm -rf wb_<modul> && tar -xzf /tmp/wb_<modul>.tar.gz
  chown -R odoo19:odoo19 wb_<modul>/
  find wb_<modul> -name __pycache__ -exec rm -rf {} +
  systemctl stop odoo19
  sudo -u odoo19 /opt/odoo19/venv/bin/python3.12 /opt/odoo19/odoo-bin \
    -c /etc/odoo19.conf -u wb_<modul> -d Main --stop-after-init
  systemctl start odoo19
'

# Logs verfolgen
ssh root@s02 'tail -f /var/log/odoo/odoo19.log | grep -E "(wb_<modul>|ERROR)"'
```

---

## 12. Sicherheits-Regeln (rote Linien)

- **Activation-Code** (Klartext) NIEMALS:
  - in der DB als Klartext-Feld
  - in `_logger`
  - in Email-Body außer im offiziellen Activation-Mail
  - im Audit-Log (`wb.license.event.details`)
- **`@license_required` umgehen** geht nicht — auch nicht für
  „nur diesen einen Edge-Case"
- **`bound_domain`/`bound_db_uuid`** sind Anti-Piracy — nicht aufweichen
- **Rate-Limits** auf öffentlichen Endpoints sind Pflicht (siehe
  `wb.rate.limit.entry` als Pattern)

---

## 13. Beispiele zum Abkupfern

| Bedarf | Datei |
|---|---|
| `_post_init_hook` mit register_install | siehe diese Datei §2 |
| `@license_required`-Nutzung | `docs/modules/wb_license_client.md` §1 |
| Test-Setup mit Fernet-Key | `wb_subscription/tests/test_install_registry.py` |
| Telegram-Push | `wb_subscription/models/wb_telegram_notifier.py` |
| Mail-Template (Markup-Body) | `wb_subscription/data/mail_template_data.xml` |
| Public HTTP-Endpoint mit Rate-Limit | `wb_subscription/controllers/api_license.py` |
| Multi-Company ir.rule | `wb_subscription/security/wb_subscription_security.xml` |
| Cron mit Jitter | `wb_license_client/models/wb_license_client.py:_cron_daily_ping` |

---

## 14. Was Du am Ende prüfen solltest (Checkliste)

- [ ] Manifest hat `wb_license_client` in `depends`
- [ ] Manifest hat `post_init_hook='_wb_<modul>_post_init'`
- [ ] `__init__.py` definiert den Hook und ruft `register_install('XXXX')`
- [ ] Mindestens 1 Methode mit `@license_required('XXXX')` versehen
- [ ] Kosten-relevante Methoden mit `min_cache_age_days=7`
- [ ] `product.product` mit `wb_is_license_product=True` + Code angelegt
- [ ] Tests mit `@tagged('wb_<modul>')` durchlaufen
- [ ] README.rst + Icon vorhanden
- [ ] DECISIONS.md Produkt-Code-Registry aktualisiert
- [ ] `python3 -m py_compile` + XML-Parse sauber
- [ ] Service-Stop vor `-u <modul>` beim Deployment

---

## 15. Pointer für tiefer einsteigen

- `ARCHITECTURE.md` — Gesamt-Architektur, Sprint-Plan
- `DECISIONS.md` — alle Architektur-Entscheidungen + Produkt-Registry
- `docs/modules/wb_subscription.md` — Server-Seite Detail
- `docs/modules/wb_license_client.md` — Client-Seite Detail
- `docs/guides/security.md` — Sicherheits-Anforderungen
- `docs/guides/deployment.md` — Detail-Deployment

---

> Wenn etwas in dieser Datei mit dem Code im Repo widerspricht: **Code
> gewinnt.** Diese Datei aktualisieren, nicht den Code zurückbiegen.
