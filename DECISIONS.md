# Entscheidungs-Log

Dieses Dokument enthält alle wichtigen Entscheidungen aus der
Architektur-Phase (Chat 23.04.2026). Format: Entscheidung + Begründung.

**Wichtig:** Diese Entscheidungen sind **fixiert**. Änderungen nur auf
explizite Tobias-Anweisung. Bei Zweifeln: fragen statt umplanen.

---

## Gesamt-Projekt

| # | Entscheidung | Begründung |
|---|---|---|
| 1 | Zwei Vertriebskanäle parallel: Odoo App Store + eigene Webseite | App Store = Reichweite, Webseite = Marge + Kundenbindung |
| 2 | App Store: Jahresversion als Einmalkauf ("ELSTER 2026") | Klarer Re-Kauf-Grund jährlich, Odoo-konform |
| 3 | Webseite: Abo mit Lizenzschlüssel, Renewal 01.01. jährlich | Planbare MRR, Standard-Kalenderjahr |
| 4 | Ersteinkauf anteilig bis 31.12. des Jahres | Alle Kunden synchron am 01.01., anteilige erste Rechnung |
| 5 | OPL-1 Lizenz (kein LGPL) | Für kommerziellen Vertrieb, Preisschutz |
| 6 | **KEIN** n8n als Bestell-Zwischenschritt | Überflüssige Abstraktion, direkter Controller analog `/api/web/lead` |

---

## Technische Basis

| # | Entscheidung | Begründung |
|---|---|---|
| 7 | Odoo-Standards nutzen: `sale.subscription`, `product.product`, `mail.template`, `account_followup`, `account.payment.term` | Kein Rad neu erfinden, Odoo-Updates profitieren mit |
| 8 | Eigenes Modell nur für lizenzspezifische Aspekte: `wb.license.key`, `.event`, `.migration`, `.trial.request` | Separate Concerns, sauberes Datenmodell |
| 9 | Zwei Module: `wb_subscription` (Server-seitig) + `wb_license_client` (bei Kunden-Odoo) | Verteiltes System, Server-Side nur lokal |

---

## Lizenzschlüssel-System

| # | Entscheidung | Begründung |
|---|---|---|
| 10 | Key-Format: `WB-{PROD}-{UUID8}{CHK}` | z.B. `WB-ELST-a3f28c919K` (17 Zeichen) |
| 11 | Kein Jahr im Key | Key bleibt stabil über Renewals |
| 12 | Checksum direkt an UUID angehängt, kein Bindestrich | Analog Luhn bei Kreditkarten / IBAN-Prüfziffer |
| 13 | 2-stellige Base32-Checksum | 1/1024 Tippfehler-Erkennung, reicht |
| 14 | Activation-Code separat: 25 Zeichen Base32 in 5er-Gruppen | Analog Windows/Adobe, Lesbarkeit |
| 15 | Activation-Code nur als bcrypt-Hash in DB | DB-Leak-Schutz |
| 16 | Activation-Code NICHT per Email versenden | Channel-Split gegen Mail-Hijacking |
| 17 | Activation-Code via Portal-Ticket + Email-OTP | Echte Kanal-Trennung mit vertretbarer UX |
| 18 | Activation-Code einmalig verwendbar ("verbrannt" nach Use) | Wiederverwendung = Alert |
| 19 | Code-Gültigkeit: 90 Tage nach Issue | Bei Nicht-Aktivierung Reminder + Ablauf |

---

## Instanz-Bindung

| # | Entscheidung | Begründung |
|---|---|---|
| 20 | Strikt 1 Odoo-Instanz pro Key | Missbrauch verhindern |
| 21 | Staffelung: mehrere Instanzen gegen Aufpreis | Monetarisierung für Reseller/Multi-Company |
| 22 | Domain-Umzug 1x/Jahr möglich, Approval durch Tobias | Flexibilität ohne Missbrauch |
| 23 | Binding via Domain + DB-UUID | Beides zusammen stabiler als nur eines |
| 24 | Fingerprint-Check (SHA256) als zusätzliche Ebene | Staging-Kopien erkennen |

---

## Trial-System

| # | Entscheidung | Begründung |
|---|---|---|
| 25 | 7 Tage Trial-Laufzeit | Lang genug zum Testen, kurz genug für Urgency |
| 26 | Lead-Capture-Pflicht (E-Mail + Firma + Tel optional) | Trial als Conversion-Turbo + Lead-Qualifizierung |
| 27 | reCAPTCHA gegen Missbrauch | Rate-Limit backup |
| 28 | Trial-Keys OHNE Activation-Code (Friction-Reduction) | Zu kurze Laufzeit für Verlust-Risiko |
| 29 | Rate-Limit: 1 Trial pro E-Mail, 1 pro Domain | Abuse-Prevention |

---

## Rechnungsstellung / Mahnungen

| # | Entscheidung | Begründung |
|---|---|---|
| 30 | Renewals: Cron 01.12., Drafts generiert, Tobias gibt manuell frei | Kontrolle, kann vor Versand stornieren/rabatieren |
| 31 | Sofort-Rechnung bei Neukauf (nicht erst Dezember) | Cashflow, Kunde erwartet Rechnung mit Produkt |
| 32 | Zahlungsziele via `account.payment.term`, pro Kunde pflegbar | Großkunden können 60 Tage, KMU 14 Tage |
| 33 | **Keine** hart kodierte Grace-Period — abgeleitet aus `account_followup` | Eine Quelle der Wahrheit, konfigurierbar in Odoo-UI |
| 34 | Mahn-Emails kommen aus Odoo-Standard, nicht eigene Templates | Konsistent mit Mahnjournal, DATEV-Export etc. |
| 35 | Unser Modul sendet nur lizenz-spezifische Mails (Grace-Start, Expired) | Separation of Concerns |

---

## Dokumente

| # | Entscheidung | Begründung |
|---|---|---|
| 36 | Lizenzzertifikat als PDF (QWeb-Report) | Formal, dokumentenwürdig |
| 37 | EULA als PDF, juristisch zu prüfen vor Release | Haftungsausschluss zwingend für Steuer-Module |
| 38 | Erweiterte Rechnung: Key + Domain pro Lizenz-Zeile | Transparenz für Buchhaltung |
| 39 | **Nur Public Key** auf Zertifikat/Rechnung, NIE Activation-Code | Security-kritisch |
| 40 | Design: weißer Hintergrund durchgängig, Navy/Cyan nur als Akzente | Wirkt seriös wie Behörden-Dokument, nicht wie Marketing-Flyer |
| 41 | QR-Code auf Zertifikat → öffentliche Verifikations-URL | Vertrauens-Signal |

---

## Verifikation

| # | Entscheidung | Begründung |
|---|---|---|
| 42 | Zweistufig: öffentlicher Link (Basis-Info) + Portal für Details | Usability + Privacy |
| 43 | reCAPTCHA auf öffentlicher Seite | Gegen Bot-Scraping |
| 44 | Portal-Login mit Email-OTP (kein Passwort-Account) | Kein extra Account-Setup für Kunden |

---

## Benachrichtigungen

| # | Entscheidung | Begründung |
|---|---|---|
| 45 | Telegram-Pushs an Tobias bei wichtigen Events (Kauf, Grace, Activation-Failed) | Mobile Info ohne Odoo-Login |
| 46 | Templates als `mail.template` (Odoo-Standard), nicht eigenes Modell | Jinja-fähig, in UI pflegbar |
| 47 | `wb.notification.log` als eigenes Modell für Audit-Trail | Compliance, Fehlersuche |

---

## Ticket-Code-Speicherung (v1.5)

| # | Entscheidung | Begründung |
|---|---|---|
| 48 | Activation-Code wird beim Ticket-Create mit **Fernet (symmetric)** verschlüsselt und in `wb.activation.ticket.encrypted_code` abgelegt | Code ist nach Erzeugung nur als bcrypt-Hash in `wb.license.key` (einweg). Für Portal-Anzeige nach OTP-Verify muss er rekonstruierbar sein — nur Fernet-Encrypt erfüllt das, ohne den Hash aufzugeben |
| 48a | Fernet-Key wird beim **Install automatisch in `ir.config_parameter`** generiert (`post_init_hook`). ENV-Variable `WB_SUBSCRIPTION_FERNET_KEY` bleibt als High-Security-Override; wenn gesetzt, wird sie bevorzugt | Plug-and-play-UX — Install funktioniert ohne manuellen Server-Eingriff. ENV bleibt die bessere Wahl (nicht im DB-Backup) und ist im README.rst dokumentiert. Der Hook ist idempotent: läuft nur beim ersten Install, nicht bei Updates — sonst würde Key-Rotation alle Pending-Tickets entwerten |
| 48b | `encrypted_code` wird **gelöscht sobald Ticket-State `consumed`** erreicht | Minimiert Angriffsfenster — nach Activation gibt es keinen wiederherstellbaren Code mehr |
| 48c | Ticket an **erste aufrufende IP gebunden** (beim GET `/activate/<ticket>` festgepinnt) | Gegen Link-Forwarding. Send-OTP und Verify-OTP nur von derselben IP |

---

## Install-Registry (Lead-Liste)

| # | Entscheidung | Begründung |
|---|---|---|
| 49 | Neuer Endpoint **`/api/license/announce`** auf `wb_subscription`. Nimmt `(product_code, domain, db_uuid, client_version, email)` und upserted in das neue Model `wb.license.install` | Lead-Liste der ungekauften Installs — wer hat das Addon installiert aber noch keine Lizenz aktiviert. Wird beim späteren Kauf via Match `(product_code, domain, db_uuid)` automatisch in `state='converted'` umgesetzt und mit der `wb.license.key` verknüpft |
| 49a | Aufruf-Strategie: **`_post_init_hook` pro Produkt-Modul** (explizit beim Install) **+ Fallback in `check_license()`** (throttled 1×/24h, falls Hook nicht durchlief) | Ein Hook ist explizit und sofortig — der Fallback fängt Server-Down beim Install ab. Doppelt hält besser, kostet nichts (HTTP-Call ist best-effort) |
| 49b | Email mitsenden: ja, `env.user.email` des Admin-Accounts | Ohne Kontakt ist die Lead-Liste nur ein Domain-Verzeichnis. Mit Email kann WB tatsächlich nachfassen |
| 49c | Opt-out via `ir.config_parameter` `wb_license_client.disable_install_registry=True`, sichtbar in Settings → WB Lizenzen → Datenschutz | DSGVO-konforme Wahlmöglichkeit für datenschutz-paranoide Kunden. Default: aus (also Announce aktiv) |
| 49d | Best-Effort: Announce-Fehler werden geschluckt, NIE als UserError geworfen | Server-Ausfall darf einen Module-Install nicht crashen. Lead-Tracking ist nice-to-have, nicht business-critical |
| 49e | Churn-Cron: Installs ohne Announce seit 60 Tagen → `state='churned'` | Lead-Hygiene. Gefiltert aus der Default-Liste, bleibt aber für Reporting erhalten |

---

## Offene Punkte für später

- EULA-Text vom Anwalt prüfen lassen (300-600 €)
- Impressum + Datenschutzerklärung auf wissen-beratung.de anpassen
- Stripe-Setup für Abo-Zahlungen
- Marketing-Assets: Screenshots, Demo-Video, Landing-Page
- Support-Kanal + SLA definieren

Details siehe `ARCHITECTURE.md` Kapitel 11.

---

## Multi-DB Self-Hosting / X-Odoo-Database-Header (2026-05-03)

`wb_license_client._do_request()` setzt seit v19.0.2.3.0 den Header `X-Odoo-Database: <self.env.cr.dbname>` auf alle HTTP-Requests an den License-Server.

**Warum:** Wenn ein Customer den License-Server und den License-Client in derselben Odoo-Instanz selbst betreibt (typisch bei WB selbst auf wissen-beratung.de) UND die Instanz mehrere Datenbanken hostet (z.B. `Main` + `wb_dev`), beantwortet Odoo HTTP-Requests ohne expliziten DB-Selektor mit 404, weil der Hostname-→-DB-Resolver mehrdeutig ist. Folge: jeder License-Ping schlug fehl, der Client-Cache `wb.license.info` flippte auf `state=unlicensed`, das gemonitorte Pro-Modul (`wb_bitwarden_pro` etc.) zeigte den User "keine gültige Lizenz" obwohl der Server-side-Key `state=active` hatte.

**Trade-Off:** Bei Standard-Setups (License-Server auf separater Box, eine DB) ist der Header eine harmlose Zusatzinfo — Odoo akzeptiert oder ignoriert ihn. Kein Migrations-Aufwand, kein Breakage.

**Alternative-Lösung verworfen:** Cache lokal befüllen ohne Ping (`apply_local_nfr`-Methode) — verletzt die Layering-Invariante "Client-Status spiegelt nur Server-Antworten wider", würde NFR-Keys an den Lifecycle-Hooks vorbei aktivieren.

---

## NFR-Lizenzen (Not For Resale, 2026-05-03)

WB-eigene Module für WB-eigene Tenants — z.B. `wb_odoo_mcp` (MCP1) auf `wissen-beratung.de` — werden über eine eigene NFR-Klasse abgerechnet, nicht über Sale-Order-Workflow:

- Neues Bool-Feld `wb.license.key.is_nfr` (default False, copy=False, tracked)
- Neue Model-Methode `wb.license.key.issue_nfr_license(product_code, bound_domain, bound_db_uuid=None, partner_id=None, valid_years=99, instance_limit=1, internal_note='')`
- Erzeugt direkt Key mit `state='active'`, `activation_hash_method='nfr'` (kein bcrypt-Code), `activated_at=now()`, ohne `sale_order_id`
- Default 99 Jahre Laufzeit ≈ perpetuell
- Default partner = `env.company.partner_id` (Self-Issue)
- Audit-Event-Type `nfr_issued` mit Details (product_code, bound_domain, valid_years)
- UI: NFR-Badge im Form-Header (`<span class="badge text-bg-info">NFR</span>`), `is_nfr`-Spalte im List-View, Search-Filter "NFR (intern/eval)" + "Verkauft (kein NFR)"

**Anwendungsregel:** NFR ist explizit für interne Selbst-Lizenzierung. Keine kommerzielle Distribution. Keine Sale-Order-Verknüpfung — fließt nicht in MRR/ARR-Reporting.

**Aufrufmuster (Odoo-Shell oder Python):**
```python
env['wb.license.key'].issue_nfr_license(
    product_code='MCP1',
    bound_domain='https://www.wissen-beratung.de',
    bound_db_uuid='<db-uuid>',
)
```

---

## Produkt-Code-Registry

**Format:** 4 Zeichen aus `[A-Z0-9]` (alphanumerisch, Großbuchstaben + Ziffern). Erweitert 2026-05-03 von rein-alphabetisch (`[A-Z]{4}`) auf alphanumerisch, um versionierte Codes wie `MCP1`, `MCP2` zu unterstützen. Bestehende Codes (TEST, TELE, BITW, …) bleiben unverändert gültig — der neue Regex ist ein Superset.

| Code | Produkt | Status |
|---|---|---|
| TEST | Test-/Demo-Produkt für Entwicklung der Lizenz-Plattform | Reserviert |
| TELE | Telnyx-Integration (Voice + SMS) für Odoo VoIP | Geplant (erstes reales Produkt nach Lizenz-Plattform) |
| DATV | DATEV Export | Konzeptionell, nicht priorisiert |
| DSGV | DSGVO Auskunft | Konzeptionell, nicht priorisiert |
| BITW | Bitwarden for Odoo (Pro): Auto-Provisioning, Plan-Detection, Cross-Company-Sharing, Compliance | In Implementierung (`wb_bitwarden_pro`, 34,95 €/Monat/Tenant) |
| MCP1 | WB Odoo MCP — Model Context Protocol Server v1 (Token-Auth, Audit-Log, MCP Spec 2026-03-26) | Live (`wb_odoo_mcp` v19.0.1.0.6, installiert auf wissen-beratung.de seit 2026-05-03) |
| VOIP | WB VoIP – Softphone, Anrufprotokoll, Telefonanlagen-Anbindung für Odoo | In Implementierung (wb_voip) |

**Historisch:** `ELST` (ELSTER UStVA/ZM) wurde 2026-04-24 verworfen — nicht weiter verfolgt. Das MVP-Skelett (`wb_elster_reports/`) bleibt als interner Prototyp archiviert.

---

## Bereits gebaute Module (Stand 23.04.2026)

### wb_odoo_automations (Produktiv, v0.5)
- Glocke bei neuem Lead ✅
- Telegram-Push bei Lead ✅
- Morning Briefing (Cron 08:00) ✅
- Followup Reminder (Cron 17:00) ✅
- Mahn-Reminder (Cron 09:00, nutzt `account_followup`) ✅
- Weekly KPI (Cron Montag 08:00) ✅
- KPI Dashboard (Backend-UI) ✅
- Wiederverwendbarer Telegram-Service ✅

### wb_elster_reports (MVP-Skelett, v1.0)
- Datenmodelle (Tax-Code, Tax-Mapping, UStVA-Report) ✅
- UStVA-Workflow (draft → computed → exported → submitted) ✅
- XML-Generator-Skelett (nach BMF-Schema, **noch nicht gegen ELSTER-Validator getestet!**)
- Backend-UI mit Liste + Formular ✅
- Security-Gruppen + ACL ✅
- Unternehmens-Stammdaten (Steuernummer, Finanzamt) ✅

**NICHT produktionsreif** — siehe Kapitel 4 in den Release-Hinweisen
(`wb_elster_reports/README.rst`).

### Geplant: wb_subscription + wb_license_client
Vertriebs-Backend und Client-Library — Architektur fertig dokumentiert,
Sprint-Plan in `ARCHITECTURE.md` Kapitel 10.

---

## Sicherheits-Härtung Sprint 1–5 (Security-Review 2026-04-28)

Vollständiger Security-Review von `wb_subscription`, `wb_license_client`,
`wb_bitwarden`, `wb_bitwarden_pro` mit 22 Befunden. Read-only-Recon auf
Production hat Pre-Launch-Status bestätigt (0 ausgegebene Lizenzen) — daher
schärfere Defaults statt Bestandskunden-freundlicher Migrationen. Gesamt-
Snapshot: `docs/snapshots/production-snapshot-2026-04-28.md`.

| # | Decision | Begründung |
|---|---|---|
| 60 | DB-Layer-Lizenz-Gate via `wb.license.gated.mixin` (Sprint 1, B-C3 / L-M1) | Decorators auf Methoden umgehbar via `ir.actions.server`, RPC, sudo() — Mixin auf `create/write/unlink` ist nicht umgehbar. `AccessError` statt `UserError` damit Server-Action-Inline-Python die Exception nicht still schluckt. |
| 61 | Access-Token NICHT in DB persistieren (Sprint 2, B-C1) | Tokens sind Session-artig, gehören nicht in DB-Backups. RAM-Cache pro Worker (`WbBitwardenAccount._token_cache`) ersetzt die früheren persistenten Felder. |
| 62 | `_safe_request()`-Wrapper mit hartem `verify=True` (Sprint 2, B-C2) | Caller können TLS-Validation nicht versehentlich abschalten. Schema-Allowlist `https://` (Cloud) bzw. `http://` (LAN-Vaultwarden mit Warning). Default-Timeout `(3, 12)` statt nacktem 15s. |
| 63 | Fernet-Master-Keys bevorzugt aus ENV mit MultiFernet-Rotation (Sprint 2, B-H1 + L-H2) | `WB_BITWARDEN_FERNET_KEY` und `WB_SUBSCRIPTION_FERNET_KEY` als ENV bevorzugt; `..._HISTORY` als Komma-CSV alter Keys für nahtlose Rotation. ENV-Werte landen nicht in DB-Backups. |
| 64 | `redact_secrets()`-Util in beiden Repos (Sprint 2, B-M1) | Event-Log-Excerpts werden gegen Fernet-Tokens, bcrypt-Hashes, JSON-/Form-encoded Secret-Felder gefiltert, bevor sie in `wb.bitwarden.event.log.response_excerpt` bzw. `wb.license.event.details` persistiert werden. |
| 65 | Replay-Schutz auf `/api/license/activate` via `request_id` (Sprint 3, L-C1) | Client schickt UUID4 mit; Server cached Resultat in `wb.license.activation_request` (TTL 7 Tage). Schutz gegen DB-Clone-Replay und versehentliche Doppel-Aktivierung. Legacy-Bucket `(key, db_uuid, domain, hour)` als Fallback. |
| 66 | Rate-Limit zweite Achse `key:` parallel zu `ip:` (Sprint 3, L-C2) | `/api/license/activate` und `/check` haben pro IP UND pro Key eigene Counter. Verhindert dass ein Angreifer mit N IPs gegen N Keys parallel scannt. |
| 67 | `_anonymize_partner_name` strippt Rechtsform-Suffixe + Längen (Sprint 3, L-C3) | Public-Verify-Page zeigt nur noch `M…` (Initial + Ellipsis). Kein Reverse-Lookup gegen Handelsregister mehr möglich. ~30 EU/US-Suffixe (GmbH, AG, KG, Inc, LLC, Ltd, SA, BV, AB, AS, …). |
| 68 | Portal-Download Whitelist `wb.license.key.allowed_portal_user_ids` (Sprint 3, L-C4) | Default: nur direkter Käufer-Kontakt darf Tarball laden. Whitelist-Eintrag erlaubt zusätzliche User. Sub-Kontakte mit gleichem `commercial_partner_id` können NICHT mehr fremde Lizenzen runterladen (war IDOR). |
| 69 | `wb.license.install` PII-Felder mit `groups=manager` (Sprint 3, L-H4) | 14 Felder (Email, Name, Telefon, USt-IdNr., Adresse, IP, User-Agent, CRM-Lead, …) sind nur für Subscription-Manager sichtbar. Server-Code (Controllers, Crons) liest weiter via `.sudo()`. |
| 70 | Setup-Wizard Rate-Limit + Audit-Log (Sprint 4, B-C4) | Max 3 Wizard-Versuche pro 24h pro User. Jeder Versuch in `wb.bitwarden.event.log` (event_type='wizard'); bei Credential-Change zusätzlich event_type='config_change' + Chatter-Eintrag — beantwortbar wer wann Bitwarden-Keys geändert hat. |
| 71 | Cross-Company-Constraint auf `wb.bitwarden.partner.collection` (Sprint 4, B-M2) | Partner mit `company_id` A darf nicht an Bitwarden-Account mit `company_id` B gebunden werden. Future Cross-Company geht über `wb.bitwarden.share.rule`. |
| 72 | Plan-Cron `limit=20` mit `plan_last_check`-Round-Robin (Sprint 4, B-H2) | Verhindert Cron-Blockade bei 100+ Accounts. Probe-Timeout `(3, 8)` statt nacktem 8s. |
| 73 | Off-Boarding-Cron erzeugt `mail.activity` bei invalid Lizenz (Sprint 4, B-H3) | Compliance: Off-Boarding darf nicht unbemerkt ausfallen, sonst bleiben Ex-Mitarbeiter sichtbar in Bitwarden. Throttled — eine Activity je Account. |
| 74 | `employee.create()` Specific-Exceptions (Sprint 4, B-H4) | `(UserError, AccessError, ValidationError, requests.RequestException)` only. Programming-Bugs (RuntimeError, IntegrityError) propagieren — Massen-Imports dürfen DB-Konsistenzfehler nicht maskieren. |
| 75 | `_inverse_client_secret` nur mit Context-Flag (Sprint 4, B-M3) | Inline-Edit von `client_secret_display` ist No-Op ohne `wb_bitwarden_explicit_secret_change=True`. Ersetzt unicode-unsichere Bullet-Detection. Setup-Wizard nutzt direkt `_cred_set`, geht am Inverse vorbei. |
| 76 | Clock-Anomaly-Detection im Offline-Cache (Sprint 5, L-H1) | `last_server_check > now + 60min` → `is_valid=False`. Schutz gegen System-Uhr-Manipulation und Snapshot-Rollback als Cache-Hold. `last_clock_anomaly_at`-Feld als forensischer Marker. |
| 77 | OTP-Versand-Audit getrennt (Sprint 5, L-M2) | Neuer event_type `otp_send_failed` — Support-Anfrage „ich hab nie eine OTP bekommen" jetzt forensisch beantwortbar (Mail-Render-Fehler, Template fehlt, send_mail-Exception). |
| 78 | Lead-Event-Log Re-Raise auf DB-Errors (Sprint 5, L-M3) | `IntegrityError`/`OperationalError` propagieren zum RPC-Caller. Logging-Layer-Fehler bleiben silent — Lead-Endpoint darf wegen kaputtem Audit-Eintrag nicht killen. |
| 79 | Zero-Bestandskunden-Verifikation in Sprint 6 | Final-Major-Bump `19.0.2.0.0` markiert Security-Hardening-Release. EULA-Update entfällt da Pre-Launch (0 ausgegebene Lizenzen) und keine User-Facing-Verschärfungen für aktive Kunden anstehen. |

**Cross-Repo-Befund während Validation gefunden (alle pre-existing, nicht durch Sprint-Code verursacht):**

| Datei | Befund | Fix |
|---|---|---|
| `wb_bitwarden_pro/wizards/.../archive_wizard.py` | Many2many-Auto-Tabellenname >63 Zeichen | Explizites `relation='wb_bw_pc_archive_wiz_mapping_rel'` (Sprint 1) |
| `wb_bitwarden_pro/views/wb_bitwarden_partner_collection_views.xml` | `<group string="...">` in Search-View Odoo-19-incompatible | String-Attribut entfernt, Group-Wrapper entfernt (Sprint 1) |
| `wb_subscription/__manifest__.py` | `crm.lead`-Feld ohne `crm` in `depends` | `crm` zur Dependency-Liste (Sprint 2) |
| Production `ir_config_parameter` | Verwaiste `wb_bitwarden.cred.4.*` von gelöschtem Account | Manueller `DELETE` auf Production + Migration für andere Tenants (Sprint 2 / B-N1) |

**Test-Coverage:** 77 `security_regression`-getaggte Tests gegen alle 22+ Bypass-Vektoren, Smoke + Test-Run jeweils auf isolierter Test-DB validiert.

---

## Test-Datenbanken: Eval und DEV-Lizenz (2026-11-20, erstmals für Home Canvas Pro)

Lizenzmodell pro Datenbank. Erkennung einer Test-DB über zwei Signale, die Odoo selbst setzt (geprüft im Odoo-19-Quellcode):

- Neue `database.uuid`: Odoo vergibt sie beim Duplizieren und beim Restore mit „Dies ist eine Kopie“ (`odoo/service/db.py`, `ir.config_parameter.init(force=True)`).
- `database.is_neutralized = True`: gesetzt beim Neutralisieren (Duplizieren mit Haken, `odoo-bin neutralize`, odoo.sh-Staging). Mails, Crons und Zahlungsanbieter sind dann aus, die DB ist produktiv kaum nutzbar.

| # | Entscheidung | Begründung |
|---|---|---|
| 80 | Meldet sich ein Key aus einer zweiten, **neutralisierten** DB (neue `db_uuid`), startet automatisch eine Eval von 14 Tagen. Banner „Testsystem, Eval bis …“ | Kunde muss Test-DBs nicht manuell melden |
| 81 | Nach 14 Tagen ist eine **DEV-Lizenz** (vergünstigt, eigenes Produkt) nötig. Sie gilt nur, solange `database.is_neutralized` gesetzt ist | Günstige DEV-Lizenz lässt sich nicht produktiv missbrauchen |
| 82 | Zweite DB **nicht** neutralisiert → zählt als zweite Produktivinstanz (Volllizenz oder Umzug nach #22). Roher `pg_dump`/Restore mit gleicher `db_uuid` und neuer Domain fällt über den Fingerprint (#24) ebenfalls hierunter | Technisch nicht von einem Serverumzug unterscheidbar |

| 83 | Eval höchstens **2 parallel** je Key, und je `db_uuid` nur **einmal** | Sonst ergäbe Neukopieren alle 14 Tage eine Dauer-Eval |
| 84 | Nicht neutralisierte fremde Instanz: **7 Tage Kulanz** (`state=grace`), danach `expired` | Echter Serverumzug fällt nicht über Nacht aus; Zeit für Migration (#22) |
| 85 | Eigenes Modell `wb.license.instance` statt `wb.license.install` | Install-Registry ist Lead-Liste mit Retention-Crons; gelöschte Einträge würden eine neue Eval ermöglichen |
| 86 | `/check` antwortet für fremde Instanzen mit `state` im alten Vokabular (`active`/`grace`/`expired`) plus neuem Objekt `instance` | Ältere Clients sperren korrekt; ein unbekannter State würde dort als `unknown` 30 Tage weiterlaufen |
| 87 | DEV-Lizenz = eigener Key mit `parent_license_id`, gebunden an die `db_uuid` der Test-DB, ausgestellt per Button an der fremden Instanz. Abrechnung über eigenes Produkt per Sale-Order | Kunde muss keinen zweiten Key eingeben; der Check mit dem Produktiv-Key findet die DEV-Lizenz |
| 88 | DEV-Lizenz läuft wie jede Lizenz bis 31.12. (#4), anteilige Erstrechnung, Verlängerung über den Renewal-Cron (#30). Unabhängig von der Laufzeit der Produktivlizenz | Ein Abrechnungsmodell für alle Lizenzen; eigenes Produkt, eigene Rechnung |
| 89 | Eval auf **14 Tage** verkürzt (statt 28). Eval (Kopie einer lizenzierten DB) und Trial (Neukunde, 7 Tage) bleiben getrennte Begriffe | Zwei Wochen reichen zum Testen; Trial und Eval haben verschiedene Zielgruppen |
| 90 | „Testlizenz“ heißt **DEV-Lizenz**: für nicht-produktive, neutralisierte DBs (Entwicklung, Staging). Status im Contract `dev` / `dev_not_neutralized` | Name sagt, wofür sie ist; vor dem ersten Deploy umbenannt, daher ohne Kompatibilitätsaufwand |
| 91 | DEV-Lizenz wird per **Sale-Order** verkauft: Button „DEV-Angebot erstellen“ an der fremden Instanz, Key entsteht bei Zahlungseingang, gebunden an deren `db_uuid`, **ohne Aktivierungscode**. DEV-Produkt = eigenes Produkt mit `wb_dev_of_tmpl_id` auf das Produktivprodukt und **ohne eigenen technischen Code**; der DEV-Key läuft unter dem Produktivprodukt | Die DB ist über den Ping mit dem Produktiv-Key bekannt, die Bindung legt WB fest (wie Auto-Bind). Technischer Code ist eindeutig (#Produkt-Code-Registry), Key-Format und Client bleiben gleich |
| 92 | **Upgrade DEV → Produktiv** über Odoos Abo-Upsell auf dem DEV-Abo: +1 Produktiv, −1 DEV, Odoo rechnet beide anteilig bis zur nächsten Abo-Rechnung. Nach Zahlung: neuer Produktiv-Key, gleiche Bindung und gleiches Enddatum; DEV-Lizenz `cancelled` mit „Umgewandelt in“. Der Client behält den alten Key: `/check` aus dieser DB antwortet wie für die neue Produktivlizenz | Kunde zahlt nur die Differenz, bereits bezahlte DEV-Zeit wird angerechnet, nicht erstattet. Kein Keywechsel beim Kunden |
| 93 | **Umzug** (DEV-DB wird per genehmigter Migration die neue Produktiv-DB): die DEV-Lizenz der Ziel-DB wird **frei** (Restlaufzeit, keine Bindung) und gilt nirgends. Die nächste neutralisierte Kopie, die sich mit dem Produktiv-Key meldet, übernimmt sie automatisch statt einer Eval | Bezahlte DEV-Laufzeit geht nicht verloren; kein manueller Schritt |

Umgesetzt in `wb_subscription` 19.0.2.12.0 und `wb_license_client` 19.0.2.5.0 (Branch `feat/test-db-eval`), #91-93 in `wb_subscription` 19.0.2.13.0 (Branch `feat/dev-license-sales`). Ohne `db_uuid` im Request (z. B. PHP-Client UMAN) bleibt alles wie bisher. Restrisiko: `database.is_neutralized` ist ein einfacher Parameter; wer ihn auf einer Produktiv-DB setzt, bekommt dort nur Eval oder DEV-Lizenz. Gehört als Klausel in die EULA.

---

## Test per Klick, Benutzer-Pakete, Englisch im Client (2026-11-26)

Entscheidungen Tobias 26.11.2026: Test automatisch 7 Tage; Pakete Starter (bis 5 Benutzer), Team (bis 20), Business (unbegrenzt).

| # | Entscheidung | Begründung |
|---|---|---|
| 94 | **Test per Klick**: Knopf „Start 7-day free trial“ im Lizenz-Assistenten des Clients. Name, E-Mail und Firma (vorbelegt aus Benutzer und Firma) gehen erst nach **ausdrücklicher Zustimmung** (Kästchen mit Hinweis auf die Datenschutzerklärung) zusammen mit Domain und DB-UUID an `/api/license/trial`. Der Server gibt **sofort frei** (`state=trial`, Laufzeit `wb_trial_days` des Produkts, gebunden an Domain + DB-UUID wie jede Lizenz, kein Aktivierungscode #28, kein API-Secret) und antwortet mit Schlüssel und Ablauf; der Client speichert ihn und ist sofort lizenziert. **1 Test je Produkt und E-Mail, Domain bzw. DB-UUID, unbefristet** (ersetzt die 24-h-Sperre aus #29; ein Test eines anderen Produkts zählt nicht), plus 5/h je IP. Derselbe Antrag erneut liefert den vorhandenen Stand. **Missbrauchsverdacht** (Wegwerf-Adresse, Domain ohne Hostnamen, DB hatte schon eine Lizenz des Produkts, ≥ 3 Anträge derselben IP in 30 Tagen) → `pending` ohne Schlüssel, Freigabe von Hand; nach der Freigabe holt ein neuer Klick den Schlüssel. Ein Test-Schlüssel in einer anderen DB bekommt **keine Eval und keine Kulanz** (#80/#84), sondern `mismatch`. Der Client lässt einen Test am `valid_to` enden, auch wenn der Server nicht erreichbar ist | Ein Klick statt Formular auf der Website; DSGVO: kein Datenversand ohne Zustimmung, nichts beim Installieren. Unbefristete Sperre, weil eine 24-h-Sperre jeden Tag einen neuen Test erlaubte. Ohne die Eval-Sperre verlängerte jede Kopie den Test |
| 95 | **Benutzergrenze je Paket**: `product.product.wb_max_users` (0 = unbegrenzt) an der Variante (Starter/Team/Business als Varianten eines Produkts; am Template nur bei einer Variante pflegbar). Die Lizenz übernimmt den Wert beim Ausstellen und beim Wechsel der Variante (`wb.license.key.max_users`, je Lizenz überschreibbar); eine spätere Änderung am Produkt ändert ausgestellte Lizenzen **nicht**. `/check`, `/activate`, `/lookup` und `/trial` melden `max_users`; der Client speichert ihn (`wb.license.info.max_users`) und stellt `wb.license.client._wb_license_max_users(code)` bereit (`None` = keine gültige Lizenz, `0` = unbegrenzt). Durchsetzen tut das Produktmodul. Ein Test bekommt das größte Paket | Server legt die Grenze fest, der Client erfindet keine. Verkaufte Pakete bleiben stabil, wenn Tobias ein Produkt umstellt. Ältere Server ohne Feld gelten als unbegrenzt, ältere Clients ignorieren es |
| 96 | **Quellsprache des Clients Englisch**, Deutsch als `i18n/de.po` (wie WB VoIP Phase 7b); Tests `test_i18n`/`test_de` prüfen Vollständigkeit und Platzhalter | Client läuft in fremden Kunden-Odoos, auch außerhalb des DACH-Raums. Die Mail-Vorlagen sind `noupdate`: bestehende Installationen behalten ihre deutschen Vorlagen |

| 97 | **Härtung nach Security-Review 26.11.2026** (ändert #94 teilweise): (I1) E-Mail-Vergleiche exakt auf kleingeschriebener Adresse bzw. `email_normalized`, nie `=ilike`; ein Testantrag legt **immer einen eigenen Kontakt** an, kein Zusammenführen mit vorhandenen Kunden aus ungeprüften Angaben. (I2) Alle „Test nicht möglich“-Fälle antworten mit **einem** Code `TRIAL_NOT_AVAILABLE` (früher `EMAIL_/DOMAIN_/DATABASE_LIMIT_REACHED`, `TRIAL_ALREADY_USED`); den vorhandenen Schlüssel gibt es erneut nur bei exakt gleicher E-Mail, Domain und DB-UUID **und** Header `X-WB-DB-UUID` derselben DB. (I3) „1 Test je Domain“ nur für öffentliche Hostnamen; bei `localhost`, IP-Adressen, `.local` u. ä. zählt nur die DB-UUID. Gleiche E-Mail oder Domain aus einer **anderen** DB → `pending` (Prüfung, Freigabe von Hand, README), nicht endgültig gesperrt; dieselbe DB → `TRIAL_NOT_AVAILABLE`. (I4) `/check`, `/activate`, `/lookup` lehnen einen Schlüssel eines anderen Produkts ab (`PRODUCT_MISMATCH`, nur wenn der Client `product_code` sendet); der Client nimmt nur Schlüssel mit Präfix `WB-<Code>-` an und wertet Antworten für ein anderes Produkt als `expired`. (I5) Öffentliche Client-Methoden sind nicht per RPC aufrufbar (`@api.private`: `check_license`, `register_install`, `submit_lead_request`, `activate_license`, `request_migration`, `auto_lookup_all_installed`, `start_trial`); die schreibenden außer `submit_lead_request` zusätzlich nur für Administratoren. `wb.license.info.key` und `server_response_raw` nur für Administratoren lesbar. (I6) `/trial` verlangt `consent: true` und `privacy_version` (`MISSING_CONSENT`), gespeichert als `consent_at`/`consent_version`; Cron entfernt die IP nach 90 Tagen und Kontaktdaten nicht umgewandelter Anträge nach 12 Monaten (`retention_trial_ip_days`, `retention_trial_contact_days`). (I7) `/order` verlangt bei mehreren Varianten `product_id` oder `default_code` (`AMBIGUOUS_PRODUCT`), API-Key-Vergleich konstantzeitig. (M3) `max_users` fail-closed: Feld fehlt = unbegrenzt (alte Server), ungültig = vorheriger Wert bleibt, bei erster Antwort wird sie verworfen; ein Test ohne `valid_to` ist ungültig | Befunde I1–I7, M3 des Reviews: Enumeration, Übernahme fremder Test-Schlüssel und Kontakte, Sperren fremder Tests über gemeinsame Hostnamen, Schlüssel-Verwechslung zwischen Produkten, RPC-Zugriff normaler Benutzer, fehlender Zustimmungsnachweis, mehrdeutige Bestellungen |

`submit_lead_request` bleibt ohne Admin-Prüfung (nur `@api.private`), weil der Lizenz-Anfrage-Assistent von Bitwarden Pro auch von Bitwarden-Administratoren ohne Einstellungsrechte genutzt wird. `check_license`/`register_install` heißen weiter so (serverseitige Aufrufer in WB VoIP, Bitwarden Pro, Home Canvas Pro, MCP, Messaging), die Logik liegt in `_check_license`/`_register_install`.

Umgesetzt in `wb_subscription` 19.0.2.14.0 und `wb_license_client` 19.0.2.7.0 (Branch `feat/testzeitraum-pakete`). Grenzen des Missbrauchsschutzes: E-Mail, Domain und DB-UUID sind frei wählbar; wer eine neue Datenbank mit neuer Domain und neuer Adresse anlegt, bekommt einen neuen Test. Das IP-Limit und die Prüfung im Backend fangen nur Serien ab.
