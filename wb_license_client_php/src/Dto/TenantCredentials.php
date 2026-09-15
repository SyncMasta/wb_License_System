<?php

declare(strict_types=1);

namespace WissenBeratung\LicenseClient\Dto;

use WissenBeratung\LicenseClient\Http\Signature;

/**
 * Zugangsdaten eines Mandanten.
 *
 * Kommen aus Umgebungsvariablen oder dem Secret Store, niemals aus dem Repo.
 * Die Klasse hat bewusst kein __toString und keine Debug-Ausgabe, die das
 * Secret zeigt.
 */
final readonly class TenantCredentials
{
    public function __construct(
        public string $key,
        public string $secret = '',
        public string $instanceId = '',
    ) {}

    public function hasSecret(): bool
    {
        return $this->secret !== '';
    }

    public function maskedKey(): string
    {
        return Signature::mask($this->key);
    }

    /**
     * Schützt vor versehentlichem Dump des Secrets (var_dump, Sentry,
     * Laravel-Exception-Page).
     *
     * @return array<string, string>
     */
    public function __debugInfo(): array
    {
        return [
            'key' => $this->maskedKey(),
            'secret' => $this->hasSecret() ? '***' : '(leer)',
            'instanceId' => $this->instanceId,
        ];
    }
}
