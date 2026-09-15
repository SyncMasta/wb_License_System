<?php

declare(strict_types=1);

namespace WissenBeratung\LicenseClient\Contracts;

use WissenBeratung\LicenseClient\Dto\TenantCredentials;

/**
 * Löst einen Mandantenschlüssel zu Zugangsdaten auf.
 *
 * Eine Dienstinstanz bedient mehrere Mandanten, je Mandant ein
 * Lizenzschlüssel. Woher die kommen — Config, Datenbank, Secret Store —
 * entscheidet der einbettende Dienst.
 */
interface TenantKeyResolver
{
    /** null, wenn der Mandant unbekannt ist. */
    public function resolve(string $tenant): ?TenantCredentials;
}
