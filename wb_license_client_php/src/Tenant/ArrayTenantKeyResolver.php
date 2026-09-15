<?php

declare(strict_types=1);

namespace WissenBeratung\LicenseClient\Tenant;

use WissenBeratung\LicenseClient\Contracts\TenantKeyResolver;
use WissenBeratung\LicenseClient\Dto\TenantCredentials;

/**
 * Mandanten aus der Konfiguration.
 *
 * Erwartet die Form aus der Config-Datei:
 *
 *     'tenants' => [
 *         'umantis-kunde-a' => [
 *             'key' => env('WB_LICENSE_KEY_KUNDE_A'),
 *             'secret' => env('WB_LICENSE_SECRET_KUNDE_A'),
 *         ],
 *     ]
 *
 * Werte gehören in Umgebungsvariablen oder den Secret Store, niemals ins Repo.
 */
final readonly class ArrayTenantKeyResolver implements TenantKeyResolver
{
    /**
     * @param  array<string, array{key?: string, secret?: string, instance_id?: string}|string>  $tenants
     */
    public function __construct(private array $tenants) {}

    public function resolve(string $tenant): ?TenantCredentials
    {
        $entry = $this->tenants[$tenant] ?? null;
        if ($entry === null) {
            return null;
        }

        // Kurzform: 'tenant' => 'WB-...' — nur Key, kein Secret.
        if (is_string($entry)) {
            return $entry === '' ? null : new TenantCredentials($entry);
        }

        $key = (string) ($entry['key'] ?? '');
        if ($key === '') {
            return null;
        }

        return new TenantCredentials(
            key: $key,
            secret: (string) ($entry['secret'] ?? ''),
            instanceId: (string) ($entry['instance_id'] ?? ''),
        );
    }
}
