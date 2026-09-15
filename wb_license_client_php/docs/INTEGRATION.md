# Integration in einen Laravel-12-Dienst

Beispiel am ersten Konsumenten: `wissen-api` überträgt WhatsApp-Bewerbungen
aus Superchat nach Abacus Umantis. Jeder Mandant hat einen eigenen
Lizenzschlüssel.

## 1. Installation

Privates Composer-Repository eintragen und installieren:

```bash
composer config repositories.wb-license vcs https://github.com/SyncMasta/wb_License_System
composer require wissen-beratung/wb-license-client:^0.1
```

Der Kern ist framework-agnostisch und braucht einen PSR-18-Client plus
PSR-17-Factories. Wer noch keine hat:

```bash
composer require guzzlehttp/guzzle nyholm/psr7
```

`php-http/discovery` findet beide automatisch — im ServiceProvider muss
nichts konfiguriert werden.

## 2. Konfiguration

```bash
php artisan vendor:publish --tag=wb-license-config
```

`.env`:

```dotenv
WB_LICENSE_URL=https://my.wissen-beratung.de
WB_LICENSE_ENFORCEMENT=warn
WB_LICENSE_SERVICE=wissen-api
WB_LICENSE_INSTANCE_ID=1f0c9a5e-...   # einmalig erzeugen, dann festhalten
WB_LICENSE_CACHE_STORE=redis

WB_LICENSE_KEY_KUNDE_A=WB-UMAN-1a2b3c4dQF
WB_LICENSE_SECRET_KUNDE_A=WBS-...
```

`config/wb-license.php`:

```php
'tenants' => [
    'umantis-kunde-a' => [
        'key'    => env('WB_LICENSE_KEY_KUNDE_A'),
        'secret' => env('WB_LICENSE_SECRET_KUNDE_A'),
    ],
],
```

**Zwei Werte, bei denen es wehtut, wenn sie falsch sind:**

`WB_LICENSE_INSTANCE_ID` wird einmal erzeugt und dann nie wieder geändert.
Geht er verloren, ändert sich die Instanzkennung aller Mandanten — im Odoo
sieht das aus wie eine Migration auf eine andere Instanz.

`WB_LICENSE_CACHE_STORE` muss Neustarts überleben. Mit `array` trägt Local
Grace nichts: bei einem Serverausfall steht der Dienst sofort auf `unknown`.

## 3. Mandanten aus der Datenbank

Wenn die Mandanten nicht in der Config stehen, den Resolver ersetzen — die
Bindings sind alle überschreibbar:

```php
// app/Providers/AppServiceProvider.php
use WissenBeratung\LicenseClient\Contracts\TenantKeyResolver;
use WissenBeratung\LicenseClient\Tenant\CallbackTenantKeyResolver;

public function register(): void
{
    $this->app->bind(TenantKeyResolver::class, fn () => new CallbackTenantKeyResolver(
        function (string $tenant): ?array {
            $row = Tenant::query()->where('slug', $tenant)->first();

            return $row === null ? null : [
                'key' => decrypt($row->license_key),
                'secret' => decrypt($row->license_secret),
            ];
        }
    ));
}
```

Wirft der Callback, gilt der Mandant als unbekannt — eine klemmende
Mandanten-Tabelle hält die Verarbeitung nicht an.

## 4. Im Verarbeitungspfad

```php
use WissenBeratung\LicenseClient\EntitlementGate;
use WissenBeratung\LicenseClient\Enum\Decision;
use WissenBeratung\LicenseClient\LicenseClient;

final class BewerbungVerarbeiten implements ShouldQueue
{
    public function handle(LicenseClient $licenses, EntitlementGate $gate): void
    {
        $status = $licenses->status($this->tenant);   // gecacht, kein Call pro Job

        match ($gate->decide($status)) {
            Decision::Process => $this->uebertragen(),

            Decision::ProcessWithWarning => tap(
                fn () => $this->uebertragen(),
                fn () => Log::warning('[umantis] ' . $gate->explain($status), [
                    'tenant' => $this->tenant,
                    'key' => $status->maskedKey(),
                ])
            )(),

            // Anhalten und alarmieren — NIEMALS verwerfen. Siehe unten.
            Decision::Hold => $this->parken($status),
        };
    }
}
```

`status()` nutzt den Cache (15 Minuten) und geht nicht bei jedem Job ans
Netz. Das ist kein Performance-Detail: `/api/license/check` schreibt
serverseitig bei jedem Aufruf eine Zeile ins Audit-Log.

## 5. Was `Hold` bedeutet

**Anhalten und alarmieren, niemals verwerfen.** Im Bewerberkontext: den
Vorgang annehmen, verschlüsselt parken, beide Seiten alarmieren, nach
Reaktivierung nacharbeiten. Es darf unter keinen Umständen dazu führen, dass
eine Bewerbung verloren geht.

Das Parken setzt der Dienst um — der Client liefert nur die Entscheidung:

```php
private function parken(LicenseStatus $status): void
{
    GeparkterVorgang::create([
        'tenant' => $this->tenant,
        'payload' => encrypt($this->rohdaten),
        'grund' => $status->state->value,
    ]);

    Notification::route('telegram', config('wb.telegram'))
        ->notify(new LizenzBlockiert($this->tenant, $status));
}
```

Ein Kommando, das nach Reaktivierung nacharbeitet, gehört dazu. Ohne das ist
das Parken nur ein langsamer Datenverlust.

## 6. Heartbeat

```php
// routes/console.php
Schedule::command('wb:license:ping')->everySixHours();
```

Der Command prüft je Mandant, ob das Intervall abgelaufen ist, und
überspringt den Rest — mehrfach am Tag aufzurufen ist deshalb unschädlich.
Er endet mit Exit-Code 0, auch wenn der Server nicht erreichbar war: ein
Ausfall unseres Odoo ist kein Fehlschlag dieses Dienstes. Mit `--strict`
lässt sich das für ein schärferes Monitoring umdrehen.

## 7. Events ins Monitoring

Die Events gehen in den Laravel-Event-Bus:

```php
// app/Providers/EventServiceProvider.php
use WissenBeratung\LicenseClient\Event\LicenseLocalGraceExpired;
use WissenBeratung\LicenseClient\Event\LicenseServerUnreachable;

protected $listen = [
    LicenseServerUnreachable::class => [MeldeAnZabbix::class],
    LicenseLocalGraceExpired::class => [AlarmiereSofort::class],
];
```

`LicenseLocalGraceExpired` ist der kritischste Alarm: ab da gibt es keinen
belastbaren Lizenzstand mehr, und im Modus `enforce` steht die Verarbeitung.
`LicenseServerUnreachable` liefert `remainingGraceSeconds` — das ist die
Zahl fürs Dashboard, nicht die bloße Tatsache des Ausfalls.

## 8. Reihenfolge der Inbetriebnahme

1. Odoo: Produkt anlegen (`wb_is_license_product`, vierstelliger
   `wb_technical_code`), Lizenzschlüssel als NFR oder direkt `active`
   erzeugen, API-Secret erzeugen und **einmalig** kopieren.
2. Dienst: Key und Secret in den Secret Store, `enforcement=warn`.
3. `php artisan wb:license:ping --force` — die Ausgabe muss `active` und
   `live` zeigen.
4. Ein paar Tage im Warn-Modus mitlaufen lassen und die Logs ansehen.
5. Erst dann `enforcement=enforce`, und erst danach serverseitig
   `wb_subscription.hmac_enforcement=required`.

Zwischen Schritt 4 und 5 gehört ein Blick in die Events: wenn dort
`LicenseServerUnreachable` auftaucht, ist der Dienst noch nicht bereit für
`enforce`.

## 9. Manueller Smoke-Test

Nicht Teil der CI — er braucht einen echten Schlüssel:

```bash
php artisan wb:license:ping umantis-kunde-a --force
```

Erwartete Ausgabe:

```
  umantis-kunde-a              active     live         process              WB-UMAN****4dQF
```

Steht dort `local_grace` statt `live`, hat der Server nicht geantwortet.
Steht dort `unknown`, prüfen in dieser Reihenfolge: Schlüssel im Odoo
vorhanden? Secret korrekt eingetragen? Systemzeit des Dienstes korrekt (der
Server toleriert 300 s Drift)? Die Logzeile benennt den Fall.
