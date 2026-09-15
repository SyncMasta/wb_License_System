<?php

declare(strict_types=1);

/**
 * Konfiguration des WB-Lizenz-Clients.
 *
 * Veröffentlichen mit:
 *     php artisan vendor:publish --tag=wb-license-config
 */
return [
    // Lizenzserver. Produktion ist my.wissen-beratung.de.
    'base_url' => env('WB_LICENSE_URL', 'https://my.wissen-beratung.de'),

    // off | warn | enforce — siehe README. Default warn, mit Absicht:
    // das Lizenzsystem ist noch nie produktiv gelaufen.
    'enforcement' => env('WB_LICENSE_ENFORCEMENT', 'warn'),

    // Wie lange eine Serverantwort als frisch gilt.
    'cache_ttl' => 900,

    // Wie lange der letzte bekannte Status ohne Server weitergilt (72 h).
    'local_grace' => 259200,

    'retries' => 2,
    'retry_base_delay_ms' => 200,

    // Sperrzeit nach TOO_MANY_REQUESTS. Das Serverfenster ist eine Stunde;
    // sofortige Wiederholungen schieben den Bucket nur weiter hoch.
    'rate_limit_cooldown' => 900,

    'circuit_threshold' => 5,
    'circuit_cooldown' => 300,

    // Heartbeat-Intervall (6 h).
    'ping_interval' => 21600,

    'service_name' => env('WB_LICENSE_SERVICE', 'wissen-api'),

    // Einmalig erzeugen und persistieren. Geht dieser Wert verloren, ändert
    // sich die Instanzkennung aller Mandanten — serverseitig sieht das aus
    // wie eine Migration auf eine andere Instanz.
    'service_instance_id' => env('WB_LICENSE_INSTANCE_ID'),

    // Cache-Store. MUSS Neustarts überleben, sonst trägt Local Grace nicht.
    // null = Default-Store der Anwendung.
    'cache_store' => env('WB_LICENSE_CACHE_STORE'),

    'tenants' => [
        // 'umantis-kunde-a' => [
        //     'key'    => env('WB_LICENSE_KEY_KUNDE_A'),
        //     'secret' => env('WB_LICENSE_SECRET_KUNDE_A'),
        // ],
    ],
];
