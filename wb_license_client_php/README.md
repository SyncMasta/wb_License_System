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

## Stand

Gebaut ist die **Signatur- und Transportschicht**:

| Klasse | Zweck |
|---|---|
| `Http\Signature` | HMAC-Signatur Schema v1, Nonce-Erzeugung, Maskierung von Schlüsseln für Logs |
| `Http\Transport` | JSON-RPC-Envelope, signierte Requests, Auswertung der beiden Fehlerebenen |
| `Http\TransportResponse` | Ergebnis mit getrennten Fehlerebenen und `retryable`-Flag |

Noch **nicht** gebaut: `LicenseClient`, `EntitlementGate`, Cache, Local Grace,
Circuit Breaker, Events, Laravel-Bridge. Die Reihenfolge ist Absicht — ohne
funktionierende Signatur wäre alles darüber auf Sand gebaut.

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

## Interop-Tests

Ohne Composer und ohne Netzwerk lauffähig:

```bash
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
