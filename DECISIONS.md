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

## Produkt-Code-Registry

| Code | Produkt | Status |
|---|---|---|
| TEST | Test-/Demo-Produkt für Entwicklung der Lizenz-Plattform | Reserviert |
| TELE | Telnyx-Integration (Voice + SMS) für Odoo VoIP | Geplant (erstes reales Produkt nach Lizenz-Plattform) |
| DATV | DATEV Export | Konzeptionell, nicht priorisiert |
| DSGV | DSGVO Auskunft | Konzeptionell, nicht priorisiert |
| BITW | Bitwarden for Odoo (Pro): Auto-Provisioning, Plan-Detection, Cross-Company-Sharing, Compliance | In Implementierung (`wb_bitwarden_pro`, 34,95 €/Monat/Tenant) |

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
