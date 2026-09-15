# Changelog

Alle nennenswerten Änderungen an `wissen-beratung/wb-license-client`.

Format nach [Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
Versionierung nach [SemVer](https://semver.org/lang/de/).

## [0.1.0] — 2026-09-15

Erste Fassung. Noch nicht gegen eine produktive Lizenz getestet — im Odoo
existiert bislang kein einziger Lizenzschlüssel.

### Hinzugefügt

- `LicenseClient` mit `status()`, `refresh()` und `ping()`. Wirft keine
  Exceptions in den Aufrufpfad; die Rückgabe ist immer ein `LicenseStatus`.
- `EntitlementGate` mit den Entscheidungen `Process`, `ProcessWithWarning`
  und `Hold`, gesteuert über den Enforcement-Modus (`off`, `warn`, `enforce`).
  Default ist `warn`.
- HMAC-Signatur (Schema v1) über `Http\Signature`, abgestimmt mit
  `wb_subscription` und `wb_license_client`.
- `Http\Transport` für den JSON-RPC-Envelope des Odoo-Servers.
  `Http\TransportResponse` trennt fachliche Fehler (HTTP 200 mit
  `result.error`) von Transport- und Serverfehlern.
- Resilienz: Cache-TTL, Local Grace (72 h), Retry mit Jitter, Circuit
  Breaker je Mandant, feste Sperre nach `TOO_MANY_REQUESTS`.
- Events `LicenseStateChanged`, `LicenseEnteredGrace`, `LicenseBlocked`,
  `LicenseServerUnreachable`, `LicenseLocalGraceExpired`.
- Mandantenfähigkeit über `TenantKeyResolver` (Array- und Callback-Variante)
  plus synthetische Instanzkennung je Mandant.
- Laravel-Bridge: ServiceProvider, Config-Publishing, `wb:license:ping`.
  Der Kern importiert nichts aus `illuminate/*`.
- Interop-Vektoren, die Client, Server und Odoo-Client gegeneinander
  festnageln.

### Bekannte Einschränkungen

- Kein Response-Signing: der Server signiert seine Antworten nicht. Die
  Signatur schützt nur die Richtung Client → Server.
- Der Smoke-Test gegen einen echten NFR-Schlüssel steht noch aus.
