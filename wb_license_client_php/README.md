# wb-license-client (PHP)

Client-Seite des WB-Lizenzsystems für PHP-Dienste. Erster Konsument ist der
Middleware-Dienst `wissen-api`, der WhatsApp-Bewerbungen aus Superchat nach
Abacus Umantis überträgt.

**Es geht hier nicht um Kopierschutz, sondern um Entitlement.** Die Dienste
laufen auf unserer Infrastruktur. Die Frage ist: Ist der Wartungs- bzw.
Abovertrag des Mandanten aktiv — und was tut der Dienst, wenn nicht.

Der Server ist das Odoo-Modul `wb_subscription` im selben Repository. Der
HTTP-Vertrag steht in [`docs/CONTRACT.md`](../docs/CONTRACT.md).

---

## Fünf Minuten

```php
$status = $client->status('umantis-kunde-a');   // gecacht
$status = $client->refresh('umantis-kunde-a');  // erzwungen
$client->ping('umantis-kunde-a');               // Heartbeat, für den Scheduler

match ($gate->decide($status)) {
    Decision::Process => $this->verarbeiten(),
    Decision::ProcessWithWarning => $this->verarbeitenUndWarnen(),
    Decision::Hold => $this->parkenUndAlarmieren(),   // NIEMALS verwerfen
};
```

In Laravel hängen `LicenseClient` und `EntitlementGate` im Container — der
ServiceProvider wird automatisch entdeckt. Vollständiges Beispiel in
[`docs/INTEGRATION.md`](docs/INTEGRATION.md).

## Aufbau

| Klasse | Zweck |
|---|---|
| `LicenseClient` | Status je Mandant, Cache, Local Grace, Retry, Circuit Breaker |
| `EntitlementGate` | State → Entscheidung, gesteuert über den Enforcement-Modus |
| `Http\Signature` | HMAC Schema v1, Nonce-Erzeugung, Maskierung für Logs |
| `Http\Transport` | JSON-RPC-Envelope, signierte Requests |
| `Http\TransportResponse` | Getrennte Fehlerebenen plus `retryable`-Flag |
| `Resilience\CircuitBreaker` | Sperre je Mandant nach wiederholten Fehlschlägen |
| `Tenant\*Resolver` | Mandanten aus Config oder aus einem Callback |
| `Laravel\*` | ServiceProvider, `wb:license:ping`. Der Kern importiert nichts aus `illuminate/*`. |

## Konfigurationsreferenz

| Schlüssel | Default | Bedeutung |
|---|---|---|
| `base_url` | `https://my.wissen-beratung.de` | Lizenzserver |
| `enforcement` | `warn` | `off`, `warn`, `enforce` — siehe unten |
| `cache_ttl` | 900 | Wie lange eine Antwort als frisch gilt |
| `local_grace` | 259200 | Wie lange der letzte Stand ohne Server weitergilt (72 h) |
| `retries` | 2 | Wiederholungen bei Transport- und Serverfehlern |
| `rate_limit_cooldown` | 900 | Sperre nach `TOO_MANY_REQUESTS` |
| `circuit_threshold` | 5 | Fehlschläge bis der Circuit öffnet |
| `circuit_cooldown` | 300 | Wie lange er offen bleibt |
| `ping_interval` | 21600 | Heartbeat-Intervall (6 h) |
| `service_name` | `wissen-api` | Geht als `domain` an den Server und in den User-Agent |
| `service_instance_id` | — | Einmalig erzeugen und festhalten |
| `cache_store` | Default-Store | Muss Neustarts überleben, sonst trägt Local Grace nicht |
| `tenants` | `[]` | Je Mandant `key` und optional `secret` |

### Enforcement-Modi

| Modus | Verhalten |
|---|---|
| `off` | Prüfung läuft, Ergebnis wird nur geloggt. Entscheidung immer `Process`. |
| `warn` | **Default.** Wie `off`, zusätzlich Alarm bei `Hold`-Zuständen. |
| `enforce` | Die Entscheidung wirkt. |

Der Default ist Absicht: dieser Dienst ist die erste produktive Nutzung des
Lizenzsystems überhaupt. Ein unerprobtes System gehört nicht ohne Notausgang
auf einen kundenkritischen Pfad.

### State → Entscheidung

| State | Entscheidung |
|---|---|
| `active`, `trial` | `Process` |
| `grace` | `ProcessWithWarning` — Rechnung überfällig |
| `issued` | `ProcessWithWarning` — Schlüssel nie aktiviert; bei einem serverseitig angelegten Schlüssel ein Einrichtungsfehler |
| `expired`, `revoked`, `cancelled` | `Hold` |
| `unknown` | `Hold` im Modus `enforce`, sonst `Process` mit Alarm |

## Resilienz

Der erste Konsument verarbeitet Bewerbungen. Ein Ausfall unseres Odoo darf
diese Verarbeitung nicht stoppen — deshalb:

- **Der Client wirft keine Exceptions in den Aufrufpfad.** Fehler landen im
  Logger und in Events, die Rückgabe ist immer ein `LicenseStatus`.
- **Local Grace:** Ist der Server nicht erreichbar, gilt der letzte bekannte
  Status bis zu 72 Stunden weiter; `source` wird dann `LocalGrace`.
- **Circuit Breaker** je Mandant: nach wiederholten Fehlschlägen wird gar
  nicht mehr angefragt, sondern direkt aus Local Grace bedient.
- **Kein Anrennen gegen das Rate-Limit:** nach `TOO_MANY_REQUESTS` wird
  15 Minuten pausiert. Das Serverfenster ist eine Stunde lang.

## Events

`LicenseStateChanged`, `LicenseEnteredGrace`, `LicenseBlocked`,
`LicenseServerUnreachable`, `LicenseLocalGraceExpired`.

Der letzte ist der kritischste: ab da gibt es keinen belastbaren
Lizenzstand mehr. `LicenseServerUnreachable` trägt `remainingGraceSeconds` —
das ist die Zahl fürs Dashboard.

In Laravel gehen alle in den Event-Bus; ohne Framework hängt man einen
eigenen `EventDispatcher` ein.

## Drei Fallen, die dieser Server stellt

**1. Kein REST, sondern JSON-RPC 2.0.** Nutzdaten stehen im Request unter
`params`, in der Antwort unter `result`. Ein flaches `{"key": "..."}` reicht
nicht durch.

**2. Der HTTP-Status ist fast immer 200 — auch bei Fehlern.** Fachliche Fehler
kommen als String in `result.error`, Odoo-Exceptions als JSON-RPC-Fehlerobjekt.
Wer auf Statuscodes entscheidet, sieht dauerhaft „alles gut". Deshalb trennt
`TransportResponse` beide Ebenen und markiert nur Transport- und Serverfehler
als `retryable`.

**3. Kein HTTP 429, kein `Retry-After`.** Rate-Limit meldet sich als
`result.error = TOO_MANY_REQUESTS`. Das Serverfenster ist eine Stunde — ein
sofortiger Retry lohnt nie und schiebt den Bucket nur weiter hoch.

## Signatur

Signiert wird der **rohe Request-Body**. Der Body muss byte-identisch gesendet
werden, wie er signiert wurde:

```php
use WissenBeratung\LicenseClient\Http\Signature;

$body = json_encode([
    'jsonrpc' => '2.0',
    'method' => 'call',
    'params' => (object) ['key' => $key],
], JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);

$headers = Signature::headers($secret, $key, $body);
// X-WB-Key, X-WB-Timestamp, X-WB-Nonce, X-WB-Signature: v1=<hex>
```

`Transport` macht das selbst — die Klasse serialisiert genau einmal und
verwendet denselben String für Signatur und Request.

Ohne Secret liefert `Signature::headers()` ein leeres Array: der Request geht
unsigniert raus, was der Server im Default-Modus toleriert. Der Client wirft
hier bewusst nicht — eine fehlende Konfiguration darf den Verarbeitungspfad
des aufrufenden Dienstes nicht anhalten.

**Systemzeit muss stimmen.** Der Server toleriert ±300 s Drift und lehnt eine
wiederverwendete Nonce ab.

## Secrets

Schlüssel und Secret kommen aus Umgebungsvariablen oder dem Secret Store,
**niemals aus dem Repo**. In Logs erscheinen sie ausschließlich maskiert:

```php
Signature::mask('WB-UMAN-1a2b3c4dQF');  // WB-UMAN****4dQF
```

## Tests

```bash
composer install
vendor/bin/pest            # Pflicht-Szenarien aus dem Übergabe-Dokument
vendor/bin/phpstan analyse # Level 8 auf src/ ohne src/Laravel
vendor/bin/pint --test
```

> **Offen:** Pint ist noch nie über diesen Code gelaufen — die Umgebung, in
> der er entstanden ist, kam nicht an die Composer-Pakete. Der Pint-Schritt in
> CI ist deshalb vorerst beratend geschaltet. Wer als Erster `composer install`
> ausführen kann: einmal `vendor/bin/pint` laufen lassen, das Ergebnis
> commiten und in `.github/workflows/php-client.yml` das
> `continue-on-error` wieder entfernen.

Ohne Composer und ohne Netzwerk lauffähig — deckt dieselben Szenarien ab:

```bash
php tests/standalone/run.php
php tests/interop/verify_vectors.php
```

`tests/interop/vectors.json` ist die gemeinsame Wahrheit für drei
Implementierungen — PHP, `wb_subscription` (Server) und `wb_license_client`
(Odoo-Client). Die Odoo-Tests prüfen gegen dieselbe Datei. Neu erzeugen nur bei
einer *gewollten* Schemaänderung:

```bash
php tests/interop/generate_vectors.php > tests/interop/vectors.json
```

## Wenn die Entscheidung `Hold` lautet

Das kommt, sobald die Entscheidungsschicht gebaut ist — die Regel gilt aber ab
dem ersten Tag und gehört hierher, weil sie fachlich und nicht technisch ist:

> **`Hold` heißt anhalten und alarmieren, niemals verwerfen.** Im
> Bewerberkontext bedeutet das: eingehende Vorgänge werden angenommen,
> verschlüsselt geparkt und beide Seiten alarmiert. Nach Reaktivierung wird
> nachgearbeitet. Es darf unter keinen Umständen dazu führen, dass eine
> Bewerbung verloren geht.

Das Parken setzt der **aufrufende Dienst** um. Der Client liefert nur die
Entscheidung.
