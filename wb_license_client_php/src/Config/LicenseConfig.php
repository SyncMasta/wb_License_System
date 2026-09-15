<?php

declare(strict_types=1);

namespace WissenBeratung\LicenseClient\Config;

use WissenBeratung\LicenseClient\Enum\EnforcementMode;

/**
 * Konfiguration des Clients.
 *
 * Die Defaults sind bewusst vorsichtig gewählt: der erste produktive Dienst
 * hängt an einem Lizenzsystem, das noch nie gelaufen ist. Enforcement steht
 * deshalb auf `warn`, und Local Grace trägt drei Tage.
 */
final readonly class LicenseConfig
{
    public function __construct(
        public string $baseUrl = 'https://my.wissen-beratung.de',
        public EnforcementMode $enforcement = EnforcementMode::Warn,
        /** Wie lange eine erfolgreiche Antwort als frisch gilt. */
        public int $cacheTtl = 900,
        /** Wie lange der letzte bekannte Status ohne Server weitergilt. */
        public int $localGrace = 259200,
        public int $retries = 2,
        /** Basiswert des exponentiellen Backoff in Millisekunden. */
        public int $retryBaseDelayMs = 200,
        /** Sperrzeit nach TOO_MANY_REQUESTS — das Serverfenster ist 1 h. */
        public int $rateLimitCooldown = 900,
        /** Fehlschläge bis der Circuit öffnet. */
        public int $circuitThreshold = 5,
        /** Wie lange der Circuit offen bleibt. */
        public int $circuitCooldown = 300,
        public int $pingInterval = 21600,
        public string $serviceName = 'wissen-api',
        public string $serviceInstanceId = '',
        public string $version = '0.1.0',
    ) {}

    /**
     * Baut die Config aus einem Array, wie es aus einer Laravel-Config-Datei
     * oder aus `env()` kommt. Unbekannte Schlüssel werden ignoriert, fehlende
     * fallen auf die Defaults zurück.
     *
     * @param  array<string, mixed>  $values
     */
    public static function fromArray(array $values): self
    {
        $int = static fn (string $key, int $default): int => isset($values[$key]) && is_numeric($values[$key])
            ? (int) $values[$key]
            : $default;
        $str = static fn (string $key, string $default): string => isset($values[$key]) && is_string($values[$key])
            ? $values[$key]
            : $default;

        $defaults = new self();

        return new self(
            baseUrl: rtrim($str('base_url', $defaults->baseUrl), '/'),
            enforcement: EnforcementMode::fromString(
                is_string($values['enforcement'] ?? null) ? $values['enforcement'] : null
            ),
            cacheTtl: $int('cache_ttl', $defaults->cacheTtl),
            localGrace: $int('local_grace', $defaults->localGrace),
            retries: max(0, $int('retries', $defaults->retries)),
            retryBaseDelayMs: $int('retry_base_delay_ms', $defaults->retryBaseDelayMs),
            rateLimitCooldown: $int('rate_limit_cooldown', $defaults->rateLimitCooldown),
            circuitThreshold: max(1, $int('circuit_threshold', $defaults->circuitThreshold)),
            circuitCooldown: $int('circuit_cooldown', $defaults->circuitCooldown),
            pingInterval: $int('ping_interval', $defaults->pingInterval),
            serviceName: $str('service_name', $defaults->serviceName),
            serviceInstanceId: $str('service_instance_id', $defaults->serviceInstanceId),
            version: $str('version', $defaults->version),
        );
    }

    /**
     * User-Agent nach dem vereinbarten Muster. Der Server schreibt ihn in
     * `last_seen_user_agent` — es ist das einzige Feld, über das sich ein
     * PHP-Dienst dort von einem Kunden-Odoo unterscheiden lässt.
     */
    public function userAgent(): string
    {
        return sprintf('wb-license-client-php/%s (%s)', $this->version, $this->serviceName);
    }

    /**
     * Stabile, synthetische Instanzkennung je Mandant.
     *
     * Tritt an die Stelle der Odoo-`db_uuid`. Die Pipe spiegelt die
     * Konvention des Servers (`SHA256(domain|db_uuid)`). Jeder Mandant
     * braucht eine eigene: eine Lizenz bindet serverseitig genau eine
     * `bound_db_uuid`.
     */
    public function instanceIdFor(string $tenant): string
    {
        return hash('sha256', $tenant . '|' . $this->serviceInstanceId);
    }
}
