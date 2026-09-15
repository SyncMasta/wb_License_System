<?php

declare(strict_types=1);

namespace WissenBeratung\LicenseClient\Http;

/**
 * Ergebnis eines Server-Calls, mit beiden Fehlerebenen getrennt.
 *
 * Der Lizenzserver quittiert fachliche Fehler mit HTTP 200 und einem
 * String in `result.error`. Ein Aufrufer muss deshalb unterscheiden
 * können zwischen:
 *
 * - `success`        — Nutzdaten liegen vor
 * - `apiError`       — Server hat geantwortet und den Request abgelehnt
 *                      (KEY_NOT_FOUND, TOO_MANY_REQUESTS, SIGNATURE_INVALID …)
 * - `serverFault`    — Odoo-Exception als JSON-RPC-Fehlerobjekt
 * - `transportFailure` — gar keine verwertbare Antwort (Timeout, DNS, TLS)
 *
 * Nur die letzten beiden rechtfertigen einen Retry; ein `apiError` wird
 * durch Wiederholen nicht besser.
 */
final readonly class TransportResponse
{
    /**
     * @param  array<string, mixed>  $data
     */
    private function __construct(
        public bool $ok,
        public array $data,
        public ?string $errorCode,
        public ?string $message,
        public int $httpStatus,
        public bool $retryable,
    ) {}

    /**
     * @param  array<string, mixed>  $data
     */
    public static function success(array $data, int $httpStatus = 200): self
    {
        return new self(true, $data, null, null, $httpStatus, false);
    }

    /**
     * @param  array<string, mixed>  $data
     */
    public static function apiError(string $code, array $data = [], int $httpStatus = 200): self
    {
        return new self(false, $data, $code, null, $httpStatus, false);
    }

    public static function serverFault(string $message, int $httpStatus = 200): self
    {
        return new self(false, [], 'SERVER_FAULT', $message, $httpStatus, true);
    }

    public static function transportFailure(string $message, int $httpStatus = 0): self
    {
        return new self(false, [], 'TRANSPORT_FAILURE', $message, $httpStatus, true);
    }

    /**
     * Rate-Limit des Servers.
     *
     * Achtung: es gibt weder HTTP 429 noch einen `Retry-After`-Header. Der
     * Backoff muss vollständig clientseitig passieren, und weil das
     * Serverfenster eine Stunde beträgt, lohnt ein sofortiger Retry nie.
     */
    public function isRateLimited(): bool
    {
        return $this->errorCode === 'TOO_MANY_REQUESTS'
            || $this->errorCode === 'TOO_MANY_ATTEMPTS';
    }

    /**
     * Signaturbezogene Ablehnung — immer ein Konfigurations- oder
     * Zeitproblem auf Clientseite, nie durch Wiederholen zu lösen.
     */
    public function isSignatureProblem(): bool
    {
        return in_array($this->errorCode, [
            'SIGNATURE_INVALID',
            'SIGNATURE_REQUIRED',
            'SIGNATURE_TIMESTAMP',
            'SIGNATURE_REPLAY',
        ], true);
    }
}
