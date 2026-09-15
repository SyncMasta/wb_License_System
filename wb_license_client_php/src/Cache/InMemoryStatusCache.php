<?php

declare(strict_types=1);

namespace WissenBeratung\LicenseClient\Cache;

use WissenBeratung\LicenseClient\Contracts\StatusCache;
use WissenBeratung\LicenseClient\Dto\LicenseStatus;

/**
 * Prozesslokaler Cache ohne externe Abhängigkeit.
 *
 * Für Tests und für Dienste ohne Redis. In einem klassischen PHP-FPM-Setup
 * überlebt er keinen Request — dort ist er nur als Notnagel brauchbar, weil
 * ohne persistenten Cache auch Local Grace nicht trägt.
 *
 * @phpstan-type Entry array{status: LicenseStatus, expires: int}
 */
final class InMemoryStatusCache implements StatusCache
{
    /** @var array<string, array{status: LicenseStatus, expires: int}> */
    private array $entries = [];

    public function get(string $tenant): ?LicenseStatus
    {
        $entry = $this->entries[$tenant] ?? null;
        if ($entry === null) {
            return null;
        }

        if ($entry['expires'] <= time()) {
            unset($this->entries[$tenant]);

            return null;
        }

        return $entry['status'];
    }

    public function put(LicenseStatus $status, int $ttlSeconds): void
    {
        $this->entries[$status->tenant] = [
            'status' => $status,
            'expires' => time() + max(1, $ttlSeconds),
        ];
    }

    public function forget(string $tenant): void
    {
        unset($this->entries[$tenant]);
    }
}
