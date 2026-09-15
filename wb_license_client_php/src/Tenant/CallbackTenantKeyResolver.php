<?php

declare(strict_types=1);

namespace WissenBeratung\LicenseClient\Tenant;

use WissenBeratung\LicenseClient\Contracts\TenantKeyResolver;
use WissenBeratung\LicenseClient\Dto\TenantCredentials;

/**
 * Mandanten aus einer beliebigen Quelle — typisch: der Datenbank des
 * aufrufenden Dienstes.
 *
 * Der Callback darf null liefern (unbekannter Mandant). Wirft er, wird das
 * als "unbekannt" behandelt statt in den Aufrufpfad durchgereicht: eine
 * klemmende Mandanten-Tabelle darf die Verarbeitung nicht anhalten.
 */
final readonly class CallbackTenantKeyResolver implements TenantKeyResolver
{
    /**
     * @param  callable(string): (TenantCredentials|array{key?: string, secret?: string, instance_id?: string}|null)  $callback
     */
    public function __construct(private mixed $callback) {}

    public function resolve(string $tenant): ?TenantCredentials
    {
        try {
            $result = ($this->callback)($tenant);
        } catch (\Throwable) {
            return null;
        }

        if ($result instanceof TenantCredentials) {
            return $result;
        }

        if (is_array($result) && ($result['key'] ?? '') !== '') {
            return new TenantCredentials(
                key: (string) $result['key'],
                secret: (string) ($result['secret'] ?? ''),
                instanceId: (string) ($result['instance_id'] ?? ''),
            );
        }

        return null;
    }
}
