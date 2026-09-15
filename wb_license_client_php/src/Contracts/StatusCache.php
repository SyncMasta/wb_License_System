<?php

declare(strict_types=1);

namespace WissenBeratung\LicenseClient\Contracts;

use WissenBeratung\LicenseClient\Dto\LicenseStatus;

/**
 * Persistenz des zuletzt bekannten Status je Mandant.
 *
 * Trägt zwei Aufgaben: Cache (spart Server-Calls) und Local Grace (hält den
 * Dienst am Laufen, wenn das Odoo nicht erreichbar ist). Deshalb muss ein
 * Eintrag deutlich länger überleben als die Cache-TTL.
 */
interface StatusCache
{
    public function get(string $tenant): ?LicenseStatus;

    public function put(LicenseStatus $status, int $ttlSeconds): void;

    public function forget(string $tenant): void;
}
