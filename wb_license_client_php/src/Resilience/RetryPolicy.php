<?php

declare(strict_types=1);

namespace WissenBeratung\LicenseClient\Resilience;

/**
 * Exponentieller Backoff mit Jitter.
 *
 * Wiederholt wird NUR, was durch Wiederholen besser werden kann: Timeouts,
 * Verbindungsfehler, Serverfehler. Ein fachlicher Fehler (KEY_NOT_FOUND,
 * SIGNATURE_INVALID) wird beim zweiten Versuch derselbe sein.
 *
 * Ausdrücklich NICHT wiederholt wird TOO_MANY_REQUESTS: der Server führt
 * Stundenfenster und schickt weder 429 noch Retry-After. Dagegen anzurennen
 * verlängert nur die eigene Sperre.
 */
final readonly class RetryPolicy
{
    public function __construct(
        private int $maxRetries = 2,
        private int $baseDelayMs = 200,
        private int $maxDelayMs = 2000,
    ) {}

    public function maxAttempts(): int
    {
        return $this->maxRetries + 1;
    }

    /**
     * Wartezeit vor dem nächsten Versuch in Mikrosekunden.
     *
     * Der Jitter (±25 %) verhindert, dass mehrere Mandanten nach einem
     * Serverausfall im Gleichschritt wieder anklopfen.
     */
    public function delayMicrosecondsFor(int $attempt): int
    {
        $delayMs = min($this->maxDelayMs, $this->baseDelayMs * (2 ** max(0, $attempt - 1)));
        $jitter = (int) round($delayMs * 0.25);
        $delayMs = random_int(max(0, $delayMs - $jitter), $delayMs + $jitter);

        return $delayMs * 1000;
    }
}
