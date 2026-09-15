<?php

declare(strict_types=1);

namespace WissenBeratung\LicenseClient\Resilience;

use Psr\SimpleCache\CacheInterface;
use WissenBeratung\LicenseClient\Contracts\Clock;

/**
 * Circuit Breaker je Mandant.
 *
 * Nach N Fehlschlägen wird für M Minuten gar nicht mehr angefragt und direkt
 * aus Local Grace bedient. Das schützt zwei Dinge: die Antwortzeit des
 * aufrufenden Dienstes (kein Warten auf Timeouts, die ohnehin scheitern) und
 * die Rate-Limit-Buckets des Servers.
 *
 * Der Zustand liegt im PSR-16-Cache, nicht im Prozessspeicher — sonst wäre
 * er in einem FPM-Setup nach jedem Request weg und der Breaker wirkungslos.
 * Die Zustände werden je Mandant getrennt geführt: ein gesperrter Mandant
 * darf die anderen nicht mitreißen.
 */
final readonly class CircuitBreaker
{
    public function __construct(
        private CacheInterface $cache,
        private Clock $clock,
        private int $threshold = 5,
        private int $cooldownSeconds = 300,
        private string $prefix = 'wb_license:circuit:',
    ) {}

    /** Ist der Circuit offen — also: Server jetzt gar nicht erst fragen? */
    public function isOpen(string $tenant): bool
    {
        $state = $this->read($tenant);
        if ($state === null) {
            return false;
        }

        if ($state['failures'] < $this->threshold) {
            return false;
        }

        return $this->clock->now()->getTimestamp() < $state['opened_at'] + $this->cooldownSeconds;
    }

    public function recordSuccess(string $tenant): void
    {
        try {
            $this->cache->delete($this->key($tenant));
        } catch (\Throwable) {
        }
    }

    public function recordFailure(string $tenant): void
    {
        $state = $this->read($tenant) ?? ['failures' => 0, 'opened_at' => 0];
        $failures = $state['failures'] + 1;

        try {
            $this->cache->set($this->key($tenant), [
                'failures' => $failures,
                // opened_at wird erst gesetzt, wenn die Schwelle erreicht ist —
                // sonst würde der Cooldown ab dem ersten Fehlschlag laufen und
                // wäre abgelaufen, bevor der Circuit überhaupt öffnet.
                'opened_at' => $failures >= $this->threshold
                    ? $this->clock->now()->getTimestamp()
                    : (int) $state['opened_at'],
            ], $this->cooldownSeconds * 2);
        } catch (\Throwable) {
        }
    }

    /** Sekunden bis der Circuit wieder schließt. 0, wenn er offen ist. */
    public function secondsUntilRetry(string $tenant): int
    {
        $state = $this->read($tenant);
        if ($state === null || $state['failures'] < $this->threshold) {
            return 0;
        }

        return max(0, $state['opened_at'] + $this->cooldownSeconds - $this->clock->now()->getTimestamp());
    }

    /**
     * Sperrt den Mandanten für eine feste Zeit, unabhängig von der Schwelle.
     *
     * Für TOO_MANY_REQUESTS: der Server führt Stundenfenster, ein sofortiger
     * zweiter Versuch schiebt den Bucket nur weiter hoch.
     */
    public function tripFor(string $tenant, int $seconds): void
    {
        try {
            $this->cache->set($this->key($tenant), [
                'failures' => $this->threshold,
                'opened_at' => $this->clock->now()->getTimestamp() - $this->cooldownSeconds + $seconds,
            ], max($seconds, $this->cooldownSeconds) * 2);
        } catch (\Throwable) {
        }
    }

    /**
     * @return array{failures: int, opened_at: int}|null
     */
    private function read(string $tenant): ?array
    {
        try {
            $raw = $this->cache->get($this->key($tenant));
        } catch (\Throwable) {
            return null;
        }

        if (! is_array($raw) || ! isset($raw['failures'], $raw['opened_at'])) {
            return null;
        }

        return ['failures' => (int) $raw['failures'], 'opened_at' => (int) $raw['opened_at']];
    }

    private function key(string $tenant): string
    {
        return $this->prefix.hash('sha256', $tenant);
    }
}
