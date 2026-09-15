# HTTP-Vertrag `wb_subscription` ↔ Lizenz-Clients

**Quelle:** Repo `SyncMasta/wb_License_System`, Modul `wb_subscription` Version `19.0.2.9.3`,
Stand `main` @ f79298c (15.09.2026).
**Zweck:** Grundlage für `wissen-beratung/wb-license-client` (PHP). Dieses Dokument beschreibt
den **Ist-Zustand des Servers**, nicht den Wunschzustand. Abweichungen vom Übergabe-Dokument
sind unter [§9 Abweichungen](#9-abweichungen-vom-übergabe-dokument) aufgeführt.

Alle Angaben stammen aus:
`wb_subscription/controllers/api_license.py`, `api_trial.py`,
`models/wb_license_key.py`, `wb_key_generator.py`, `wb_rate_limit_entry.py`,
`wb_license_install.py`.

---

## 1. Transport — das Wichtigste zuerst

Alle `/api/...`-Routen sind Odoo-Routen mit `type='json'`. Das ist **kein REST**, sondern
**JSON-RPC 2.0**. Das hat vier Konsequenzen, die den PHP-Client-Kern bestimmen:

1. **Request-Body muss ein JSON-RPC-Envelope sein.** Die Controller-Kwargs stehen unter `params`:

   ```json
   {"jsonrpc": "2.0", "method": "call", "params": {"key": "WB-UMAN-1a2b3c4dQF"}}
   ```

   Ein flaches `{"key": "..."}` funktioniert **nicht** — Odoo würde die Felder nicht als
   Kwargs durchreichen.

2. **Die Nutzdaten stehen in der Antwort unter `result`.**

   ```json
   {"jsonrpc": "2.0", "id": null, "result": {"state": "active", ...}}
   ```

3. **Der HTTP-Status ist fast immer `200` — auch bei Fehlern.** Fachliche Fehler kommen als
   `result.error` (String-Code), Server-Exceptions als JSON-RPC-`error`-Objekt, ebenfalls mit
   HTTP 200. Ein PHP-Client, der auf Statuscodes entscheidet, sieht dauerhaft „alles gut".
   **Entscheidungslogik muss auf `result` basieren, nicht auf dem Status.**

4. **`Content-Type: application/json` ist Pflicht**, sonst routet Odoo den Request nicht als JSON.

| Eigenschaft | Wert |
|---|---|
| Methode | ausschließlich `POST` (`methods=['POST']`) |
| Auth | `auth='public'` — kein Login, kein Token, kein HMAC (siehe §3) |
| CSRF | aus (`csrf=False`) |
| CORS | `*` für alle `/api/license/*`; `*.wissen-beratung.de` für `/api/license/trial` und `/api/wb_subscription/order` |
| Base-URL Produktion | `https://my.wissen-beratung.de` (das Client-Odoo-Modul nutzt als Default noch `https://wissen-beratung.de` — für PHP `my.` konfigurieren) |

### Header, die der bestehende Odoo-Client sendet

Nicht serverseitig ausgewertet (kein Controller liest sie), aber für Reverse-Proxy und Log-Forensik
sinnvoll — der PHP-Client sollte sie nachbilden:

```
Content-Type: application/json
User-Agent: wb_license_client/<version>        → PHP: wb-license-client-php/<version> (<service-name>)
X-WB-DB-UUID: <db_uuid>
X-WB-Domain: <domain>
X-Odoo-Database: <dbname>                      → Multi-DB-Routing-Hint, für PHP irrelevant (weglassen)
```

`User-Agent` wird vom `/check`-Endpunkt in `wb.license.key.last_seen_user_agent` (max. 255 Zeichen)
und im Event-Log persistiert. Er ist damit das einzige Feld, über das wir einen PHP-Dienst
serverseitig von einem Kunden-Odoo unterscheiden können — bitte sprechend belegen.

---

## 2. Endpunkt-Übersicht

| Route | Für den PHP-Client | Seiteneffekt | Rate-Limit (Default) |
|---|---|---|---|
| `POST /api/license/check` | **ja — der einzige benötigte Endpunkt** | schreibend (Ping-Metadaten, Event) | 100/h pro IP **und** 200/h pro Key |
| `POST /api/license/lookup` | nein (Zero-Touch-Bind für Odoo-Installs) | schreibend (bindet Lizenz!) | 10/h pro IP |
| `POST /api/license/activate` | nein (Code-Flow, laut Übergabe Nicht-Ziel) | schreibend | 5/h pro IP **und** 5/h pro Key |
| `POST /api/license/announce` | nein (Lead-Registry) | schreibend | 20/h pro IP |
| `POST /api/license/lead` | **nein — nicht verwenden**, siehe §8 | schreibend (legt `crm.lead` an) | 5/h pro IP |
| `POST /api/license/migrate` | nein | schreibend | 2/Tag pro Key |
| `POST /api/license/trial` | nein (nur WB-Website) | schreibend | 1/24h je Email und Domain |
| `POST /api/wb_subscription/order` | nein (nur WB-Website, `X-API-Key`) | schreibend | – |

Alle Defaults sind über `ir.config_parameter` `wb_subscription.rate_limit_<endpoint>` im Format
`"<max>/<window_seconds>"` überschreibbar (`endpoint` ∈ `check`, `check_key`, `activate`,
`activate_key`, `announce`, `lead`, `lookup`, `migrate`). Der Client darf sich also nicht auf
die Defaults verlassen.

---

## 3. Authentifizierung — Klartext

**Es gibt keine.** Alle `/api/license/*`-Routen laufen mit `auth='public'`. Die einzigen
Schutzschichten sind:

1. **Format-Check des Public Keys** (reine CPU-Prüfung, kein DB-Zugriff):
   `^WB-([A-Z0-9]{4})-([a-f0-9]{8})([A-Z2-7]{2})$` plus 2-stellige Base32-Prüfsumme über
   `UPPERCASE("WB-<PROD>-<uuid8>")`, Alphabet `ABCDEFGHIJKLMNOPQRSTUVWXYZ234567`,
   Summe der Zeichencodes mod 1024 → `alphabet[n//32] + alphabet[n%32]`.
2. **Rate-Limits** (§2) über `wb.rate.limit.entry`.
3. **Kenntnis des Keys selbst** — der Key ist das Geheimnis.

Damit sind die Antworten auf die offenen Fragen aus §0 der Übergabe:

| Frage | Antwort aus dem Code |
|---|---|
| Wie authentifiziert sich der Client? | Gar nicht. Nur Public Key im Body. Kein Shared Secret, kein HMAC, keine Signatur über den Body. |
| Signiert der Server seine Antwort? | Nein. Kein Signatur-Feld, kein JWS, keine Header. Vertrauen hängt allein an TLS. |
| Fingerprint-Schema exakt? | `sha256(f"{domain.lower().strip()}\|{db_uuid.strip()}")` als Hex-Digest (`wb.key.generator.compute_fingerprint`). Trennzeichen ist ein Pipe, **Protokoll und Trailing Slash werden hier _nicht_ entfernt** — nur beim Auto-Bind-Matching (`_normalize_domain`: lowercase, `http(s)://` ab, Trailing Slash ab). **Für den PHP-Client irrelevant:** der Fingerprint wird ausschließlich serverseitig berechnet und nie übertragen. |
| Ist `bound_db_uuid` produktweit eindeutig? | Nein. Einzige DB-Constraint ist `UNIQUE(name)` auf dem Key. Eine Lizenz bindet aber genau **eine** `bound_db_uuid` (`_lookup_for_auto_bind` matcht nur bei `bound_db_uuid = False` oder identisch). Bei mehreren Mandanten auf einer Dienstinstanz braucht also **jeder Mandant eine eigene synthetische Instanzkennung** — der Vorschlag `SHA256(tenant_slug \| service_instance_id)` trägt. |
| Reiner Statusendpunkt ohne Seiteneffekt? | **Existiert nicht.** `/api/license/check` ist Status _und_ Ping in einem: er schreibt `last_seen_at`, `last_seen_ip`, `last_seen_user_agent`, inkrementiert `ping_count_total` und legt pro Aufruf ein `wb.license.event` vom Typ `ping` an. |

Konsequenz für den PHP-Client: `status()`, `refresh()` und `ping()` treffen **denselben**
Endpunkt. `ping()` ist kein separater Call, sondern ein `refresh()`, dessen Ergebnis verworfen
werden kann. Jeder Cache-Miss erzeugt eine Audit-Zeile im Odoo — das spricht für den 15-Minuten-Cache
und gegen aggressives Refreshen.

---

## 4. `POST /api/license/check` — Status + Heartbeat

Der einzige Endpunkt, den der PHP-Client benötigt.

### Request (`params`)

| Feld | Typ | Pflicht | Bedeutung |
|---|---|---|---|
| `key` | string | **ja** | Public Key, Format siehe §3 |
| `domain` | string | nein | nur Event-Log; zusammen mit `db_uuid` + `module_version` Update der Install-Version |
| `db_uuid` | string | nein | dito — hier die synthetische Instanzkennung |
| `module_version` | string | nein | Version des Konsumenten; nur wirksam wenn `domain` **und** `db_uuid` mitkommen |

Weitere Felder werden ignoriert (`**kw`).

### Response (Erfolg, in `result`)

| Feld | Typ | Bemerkung |
|---|---|---|
| `state` | string | `issued` \| `trial` \| `active` \| `grace` \| `expired` \| `revoked` \| `cancelled` |
| `valid_from` | string \| null | ISO-**Date** (`YYYY-MM-DD`), kein Zeitstempel |
| `valid_to` | string \| null | ISO-Date |
| `grace_until` | string \| null | ISO-Date; nur ≠ null wenn `state = 'grace'`. Abgeleitet aus `partner.followup_line_id.delay`, sonst heute + 7 Tage — **wird bei jedem Lesen neu berechnet**, ist also ein gleitendes Datum, kein fixer Termin. |
| `product_code` | string | 4 Zeichen |
| `latest_module_version` | string \| null | gepflegte Soll-Version des Produkts |
| `download_url` | string \| null | Portal-Link, nur gesetzt wenn `latest_module_version` ≠ `module_version` |

Die letzten beiden Felder sind für PHP-Dienste gegenstandslos (kein Update-Mechanismus, §1 der
Übergabe) — in `raw` mitführen, sonst ignorieren.

### Fehler (`result.error`, HTTP weiterhin 200)

| Code | Ursache | Empfohlene Client-Reaktion |
|---|---|---|
| `TOO_MANY_REQUESTS` | IP- oder Key-Limit überschritten | kein Retry, Circuit Breaker öffnen, `LicenseServerUnreachable`, aus Cache/LocalGrace bedienen |
| `INVALID_KEY_FORMAT` | Formatprüfung gescheitert | kein Retry — Konfigurationsfehler, deutlich loggen (maskiert!), `unknown` |
| `KEY_NOT_FOUND` | Key existiert nicht in `wb.license.key` | kein Retry, deutlicher Log-Eintrag, `unknown` |

**Es gibt kein HTTP 429 und keinen `Retry-After`-Header.** Der Backoff muss vollständig
clientseitig erfolgen; sinnvoll ist eine feste Sperre (z. B. 15 Minuten) nach `TOO_MANY_REQUESTS`,
weil das Serverfenster eine Stunde beträgt.

### Beispiel

```bash
curl -sS https://my.wissen-beratung.de/api/license/check \
  -H 'Content-Type: application/json' \
  -H 'User-Agent: wb-license-client-php/0.1.0 (wissen-api)' \
  -d '{"jsonrpc":"2.0","method":"call","params":{
        "key":"WB-UMAN-1a2b3c4dQF",
        "domain":"wissen-api.wissen-beratung.de",
        "db_uuid":"9f1c...(synthetisch, je Mandant)",
        "module_version":"0.1.0"}}'
```

```json
{"jsonrpc":"2.0","id":null,"result":{
  "state":"active","valid_from":"2026-01-01","valid_to":"2026-12-31",
  "grace_until":null,"product_code":"UMAN",
  "latest_module_version":null,"download_url":null}}
```

Fehlerfall:

```json
{"jsonrpc":"2.0","id":null,"result":{"error":"KEY_NOT_FOUND"}}
```

Server-Exception (z. B. DB nicht erreichbar) — ebenfalls HTTP 200:

```json
{"jsonrpc":"2.0","id":null,"error":{"code":200,"message":"Odoo Server Error","data":{...}}}
```

---

## 5. `POST /api/license/lookup` — nur zur Kenntnis

Zero-Touch-Bind für Odoo-Installationen. **Der PHP-Client darf ihn nicht aufrufen**: ein Treffer
bindet die Lizenz unwiderruflich an die übergebene `db_uuid`/`domain`, setzt `state` von `issued`
auf `active`, löscht den `activation_hash` und entschärft `auto_bind_armed`.

Request: `product_code` (4 Zeichen, Pflicht), `db_uuid` (Pflicht), `domain` und/oder `email`.
Antwort bei Treffer: `{"found": true, "key": "...", "state": "...", "valid_from": ..., "valid_to": ...}` —
**enthält den Public Key im Klartext**, deshalb das strenge Limit von 10/h pro IP.
Ohne Treffer: `{"found": false}` plus Event `auto_bind_lookup_failed`.
Fehler: `TOO_MANY_REQUESTS`, `INVALID_PRODUCT_CODE`, `MISSING_BINDING_DATA`, `MISSING_MATCH_CRITERIA`.

Matching-Regeln: `product_code` exakt, `auto_bind_armed = True`, `auto_bind_armed_until > now`,
`state ∈ {issued, active}`, `bound_db_uuid` leer oder identisch, und dann
normalisierte `pre_assigned_domain` == Domain **oder** `pre_assigned_email` == Email (lowercase).

---

## 6. Übrige Endpunkte (Vollständigkeit)

- **`/api/license/activate`** — `key`, `activation_code` (`^[A-HJ-NP-Z2-9]{5}(-[A-HJ-NP-Z2-9]{5}){4}$`),
  `domain`, `db_uuid`, vier Pflicht-Consents (`confirm_eula`, `confirm_terms`, `confirm_privacy`,
  `confirm_no_refund`), optional `subscribe_newsletter`, `contact_email`, `request_id` (UUID4,
  Replay-Dedup). Erfolg: `{"status":"ok","state":...,"bound_domain":...,"valid_to":...}`.
  Fehler: `TOO_MANY_ATTEMPTS`, `INVALID_KEY_FORMAT`, `INVALID_CODE_FORMAT`, `MISSING_BINDING_DATA`,
  `MISSING_CONSENTS` (+ `missing`-Liste), `KEY_NOT_FOUND`. Code wird bcrypt-geprüft (12 Runden).
  **Laut Übergabe §1 Nicht-Ziel — im PHP-Paket nicht implementieren.**
- **`/api/license/announce`** — `product_code`, `domain`, `db_uuid`, optional `email`,
  `client_version`. Legt/aktualisiert einen `wb.license.install`-Lead-Eintrag. Für unsere eigene
  Infrastruktur sinnlos.
- **`/api/license/migrate`** — `key` + `new_domain`/`new_db_uuid`/`reason`/`contact_email`.
  Legt einen Migrationsantrag an; die Umbindung macht WB manuell im Odoo.
- **`/api/license/trial`**, **`/api/wb_subscription/order`** — CORS auf `*.wissen-beratung.de`,
  gehören zur Website. Der Order-Endpunkt ist der einzige mit echter Auth (`X-API-Key` gegen
  `ir.config_parameter wb_subscription.webshop_api_key`).

---

## 7. State-Semantik für die Entscheidungsschicht

Die Server-States entsprechen exakt dem Enum aus der Übergabe. Zwei Punkte, die die Zuordnung
schärfen:

- `issued` heißt: Key existiert, wurde aber **nie aktiviert** (`activated_at` leer). Bei
  NFR-Keys (`is_nfr = True`) wird direkt `active` erzeugt, ohne Activation-Code. Da PHP-Dienste
  keinen Aktivierungsflow haben, werden ihre Keys im Odoo direkt als `active` oder NFR angelegt —
  `issued` sollte im Betrieb gar nicht auftreten und ist damit ein Hinweis auf einen
  Einrichtungsfehler. Die Einstufung als `ProcessWithWarning` bleibt richtig, der Warntext sollte
  aber „Lizenz nie aktiviert" sagen, nicht „noch nicht in Betrieb".
- `grace` wird nicht vom Client ausgelöst, sondern vom Cron `_cron_update_states` aus dem
  Mahnstatus des Partners. `grace_until` gleitet mit (§4) — der Client darf daraus **keine**
  lokale Ablauffrist ableiten, sondern nur anzeigen.

---

## 8. Sicherheitsbefunde beim Lesen der Controller

Meldepflichtig gemäß §0 der Übergabe:

1. **`/api/license/lead` schreibt unauthentifiziert ins CRM.** Der Endpunkt legt ohne jede
   Authentifizierung einen `crm.lead` bzw. eine Verkaufschance an (`wb.license.install.submit_lead`
   → `_ensure_crm_lead`), gedrosselt nur durch 5/h pro IP. Er ist damit das Muster, das im April
   2026 zum Dublettenproblem geführt hat. Der PHP-Client spricht ihn **nicht** an.
2. **`/api/wb_subscription/order` matcht `res.partner` per `email =ilike`** und legt bei
   Nichttreffer einen neuen Partner an — genau der E-Mail-basierte Identitätsabgleich, vor dem die
   Übergabe warnt. Immerhin hinter `X-API-Key`. Kein Client-Thema, aber ein offener Punkt für das
   Serverrepo.
3. **`/api/license/check` prüft keine Bindung.** Wer den Key kennt, bekommt den Status und
   überschreibt dabei `last_seen_ip`/`last_seen_user_agent` der Lizenz. `domain`/`db_uuid` im Body
   werden **nicht** gegen `bound_domain`/`bound_db_uuid` validiert. Der Key ist alles.
   → Für den PHP-Client: Keys gehören in den Secret Store, in Logs ausschließlich maskiert
   (`WB-UMAN-****QF`), und nie in Exception-Messages oder Event-Payloads.
4. **Kein Response-Signing.** Ein kompromittierter Zwischenweg kann `state` frei setzen. Bei
   Enforcement-Modus `enforce` ist das ein Verfügbarkeits- und Missbrauchsrisiko in beide
   Richtungen. TLS-Verifikation ist im PHP-Client daher nicht abschaltbar zu konfigurieren.
5. **Kein `Retry-After`, kein 429.** Rate-Limit-Überschreitung ist von einem normalen Ergebnis nur
   am `result.error`-String unterscheidbar. Ein naiver Client kann sich dauerhaft selbst aussperren.

---

## 9. Abweichungen vom Übergabe-Dokument

| Übergabe nimmt an | Tatsächlich |
|---|---|
| REST mit flachem JSON-Body | JSON-RPC 2.0, Nutzdaten unter `params` / `result` |
| Aussagekräftige HTTP-Statuscodes (401/403/404/429/500) | praktisch immer 200; Fehler als String-Code in `result.error` |
| HTTP 429 mit `Retry-After` | existiert nicht — `{"error":"TOO_MANY_REQUESTS"}` |
| HTTP 404 bei unbekanntem Key | `{"error":"KEY_NOT_FOUND"}` mit Status 200 |
| HTTP 401/403 | existiert nicht, weil es keine Authentifizierung gibt |
| separater Ping-Endpunkt | `/api/license/check` ist Status und Ping zugleich |
| `validFrom`/`validTo`/`graceUntil` als Zeitstempel | reine Datumswerte (`YYYY-MM-DD`) |
| Client berechnet Fingerprint | Client überträgt nur `domain` + `db_uuid`; Fingerprint entsteht serverseitig |

Die Testszenarien aus §11 der Übergabe bleiben gültig, ihre **Auslöser** ändern sich:
„HTTP 401/403" und „HTTP 404" werden zu `result.error`-Fällen, „HTTP 429 mit `Retry-After`" zu
„`TOO_MANY_REQUESTS` ohne Header → clientseitige Sperre". „HTTP 500" bleibt als echter Statusfall
bestehen (Reverse Proxy, Odoo down) und ist zusätzlich als JSON-RPC-`error`-Objekt mit Status 200
zu testen.

---

## 10. Offene Punkte für die Abnahme

1. **Bestätigung, dass der PHP-Client ausschließlich `/api/license/check` nutzt.** Alles andere
   ist entweder schreibend auf Lizenzbindungen oder CRM-relevant.
2. **Keine Auth heißt: der Key ist das einzige Geheimnis.** Soll das für die erste produktive
   Nutzung so bleiben, oder soll der Server vorher ein Shared Secret bzw. eine HMAC-Signatur
   über den Body bekommen? Das ist eine Serverrepo-Entscheidung und blockiert den Client nicht —
   ein `Http/Signature.php` wird aber nur dann gebaut, wenn die Antwort „ja" lautet.
3. **`db_uuid`-Schema.** Vorschlag bestätigt: `hash('sha256', $tenantSlug . '|' . $serviceInstanceId)`,
   damit die Pipe-Konvention des Servers gespiegelt wird. `domain` = der feste Hostname des
   Dienstes, nicht die Kundendomäne.
4. **Anlage der Keys im Odoo.** `wb.license.key` enthält null Datensätze; ein `product.product`
   mit `wb_is_license_product = True` und vierstelligem `wb_technical_code` (Vorschlag `UMAN`)
   muss vor dem Smoke-Test existieren. Key als NFR oder direkt `active` anlegen, damit kein
   Activation-Code-Flow nötig wird.
5. **Betriebsfrage `ping_count_total`:** Bei 15-Minuten-Cache und 6-Stunden-Heartbeat pro Mandant
   entstehen pro Mandant ~4 Events/Tag im `wb.license.event`-Audit-Log. Bei vielen Mandanten ist
   das eine wachsende Tabelle ohne Aufräum-Cron — bitte im Serverrepo vormerken.
