<?php

declare(strict_types=1);

namespace WissenBeratung\LicenseClient\Cache;

use Psr\SimpleCache\CacheInterface;
use Psr\SimpleCache\InvalidArgumentException;
use WissenBeratung\LicenseClient\Contracts\StatusCache;
use WissenBeratung\LicenseClient\Dto\LicenseStatus;

/**
 * StatusCache auf Basis eines PSR-16-Caches (im Zieleinsatz Redis).
 *
 * Jeder Zugriff ist gekapselt: ein kaputter oder nicht erreichbarer Cache
 * darf den Verarbeitungspfad des Dienstes nicht anhalten. Fällt er aus,
 * verhält sich der Client, als wäre nichts gespeichert — dann gibt es eben
 * einen Server-Call mehr.
 */
final readonly class PsrStatusCache implements StatusCache
{
    public function __construct(
        private CacheInterface $cache,
        private string $prefix = 'wb_license:',
    ) {}

    public function get(string $tenant): ?LicenseStatus
    {
        try {
            $raw = $this->cache->get($this->key($tenant));
        } catch (InvalidArgumentException) {
            return null;
        } catch (\Throwable) {
            return null;
        }

        return LicenseStatus::fromArray($raw);
    }

    public function put(LicenseStatus $status, int $ttlSeconds): void
    {
        try {
            $this->cache->set($this->key($status->tenant), $status->toArray(), $ttlSeconds);
        } catch (\Throwable) {
            // Absichtlich still: ein nicht schreibbarer Cache ist ein
            // Performance-Problem, kein Grund den Request scheitern zu lassen.
        }
    }

    public function forget(string $tenant): void
    {
        try {
            $this->cache->delete($this->key($tenant));
        } catch (\Throwable) {
        }
    }

    /**
     * Cache-Key je Mandant. Der Mandantenschlüssel wird gehasht, damit
     * weder Sonderzeichen den Key-Namespace sprengen noch Mandantennamen
     * im Redis-Keyspace auftauchen.
     */
    private function key(string $tenant): string
    {
        return $this->prefix . 'status:' . hash('sha256', $tenant);
    }
}
