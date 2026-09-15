<?php

declare(strict_types=1);

namespace WissenBeratung\LicenseClient;

use Psr\Log\LoggerInterface;
use Psr\Log\NullLogger;
use WissenBeratung\LicenseClient\Config\LicenseConfig;
use WissenBeratung\LicenseClient\Contracts\Clock;
use WissenBeratung\LicenseClient\Contracts\EventDispatcher;
use WissenBeratung\LicenseClient\Contracts\LicenseTransport;
use WissenBeratung\LicenseClient\Contracts\StatusCache;
use WissenBeratung\LicenseClient\Contracts\TenantKeyResolver;
use WissenBeratung\LicenseClient\Dto\LicenseStatus;
use WissenBeratung\LicenseClient\Dto\TenantCredentials;
use WissenBeratung\LicenseClient\Enum\LicenseState;
use WissenBeratung\LicenseClient\Enum\StatusSource;
use WissenBeratung\LicenseClient\Event\LicenseEnteredGrace;
use WissenBeratung\LicenseClient\Event\LicenseLocalGraceExpired;
use WissenBeratung\LicenseClient\Event\LicenseServerUnreachable;
use WissenBeratung\LicenseClient\Event\LicenseStateChanged;
use WissenBeratung\LicenseClient\Event\NullEventDispatcher;
use WissenBeratung\LicenseClient\Http\TransportResponse;
use WissenBeratung\LicenseClient\Resilience\CircuitBreaker;
use WissenBeratung\LicenseClient\Resilience\RetryPolicy;

/**
 * Lizenzstatus je Mandant, gecacht und ausfalltolerant.
 *
 * **Diese Klasse wirft keine Exceptions in den Aufrufpfad.** Der erste
 * Konsument verarbeitet Bewerbungen; ein Ausfall unseres Odoo darf diese
 * Verarbeitung nicht stoppen. Jeder Fehler landet im Logger und in Events,
 * die Rückgabe ist immer ein LicenseStatus — im schlimmsten Fall mit
 * `state = unknown`.
 *
 * Der Server kennt keinen seiteneffektfreien Statusendpunkt: `/api/license/check`
 * ist Status und Ping in einem und schreibt bei jedem Aufruf Ping-Metadaten
 * plus eine Zeile ins Audit-Log. `status()`, `refresh()` und `ping()` treffen
 * deshalb denselben Endpunkt — der Cache ist hier kein Luxus, sondern das,
 * was das Audit-Log des Odoo vor dem Volllaufen bewahrt.
 */
final class LicenseClient
{
    private readonly RetryPolicy $retryPolicy;

    public function __construct(
        private readonly LicenseConfig $config,
        private readonly LicenseTransport $transport,
        private readonly TenantKeyResolver $resolver,
        private readonly StatusCache $cache,
        private readonly Clock $clock,
        private readonly ?CircuitBreaker $circuitBreaker = null,
        private readonly EventDispatcher $events = new NullEventDispatcher(),
        private readonly LoggerInterface $logger = new NullLogger(),
        ?RetryPolicy $retryPolicy = null,
    ) {
        $this->retryPolicy = $retryPolicy ?? new RetryPolicy(
            maxRetries: $config->retries,
            baseDelayMs: $config->retryBaseDelayMs,
        );
    }

    /**
     * Status des Mandanten. Nutzt den Cache, solange er frisch ist.
     */
    public function status(string $tenant): LicenseStatus
    {
        $cached = $this->cache->get($tenant);
        if ($cached !== null && $cached->ageInSeconds($this->clock->now()) < $this->config->cacheTtl) {
            return $cached->withSource(StatusSource::Cache);
        }

        return $this->refresh($tenant);
    }

    /**
     * Erzwingt einen Server-Call, egal wie frisch der Cache ist.
     */
    public function refresh(string $tenant): LicenseStatus
    {
        $credentials = $this->resolver->resolve($tenant);
        if ($credentials === null) {
            $this->logger->error('[wb-license] Unbekannter Mandant: {tenant}', ['tenant' => $tenant]);

            return LicenseStatus::unknown($tenant, '', $this->clock->now());
        }

        $cached = $this->cache->get($tenant);

        // Circuit offen: gar nicht erst fragen, direkt aus Local Grace bedienen.
        if ($this->circuitBreaker?->isOpen($tenant) === true) {
            $this->logger->info('[wb-license] Circuit offen für {tenant}, kein Server-Call', [
                'tenant' => $tenant,
                'retry_in' => $this->circuitBreaker->secondsUntilRetry($tenant),
            ]);

            return $this->fallback($tenant, $credentials, $cached, 'circuit_open');
        }

        $response = $this->callWithRetries($tenant, $credentials);

        if ($response->ok) {
            $this->circuitBreaker?->recordSuccess($tenant);
            $status = LicenseStatus::fromServerPayload(
                $tenant,
                $credentials->key,
                $response->data,
                $this->clock->now(),
            );
            $this->emitTransitions($cached, $status);
            // TTL = Local Grace: der Eintrag muss die Cache-Frische deutlich
            // überleben, sonst gibt es bei einem Serverausfall nichts mehr,
            // worauf Local Grace zurückgreifen könnte.
            $this->cache->put($status, $this->config->localGrace);

            return $status;
        }

        // Rate-Limit: der Server führt Stundenfenster und schickt weder 429
        // noch Retry-After. Dagegen anrennen verlängert nur die Sperre.
        if ($response->isRateLimited()) {
            $this->circuitBreaker?->tripFor($tenant, $this->config->rateLimitCooldown);
            $this->logger->warning('[wb-license] Rate-Limit für {tenant}, Pause {cooldown}s', [
                'tenant' => $tenant,
                'cooldown' => $this->config->rateLimitCooldown,
                'key' => $credentials->maskedKey(),
            ]);
        } elseif ($response->retryable) {
            $this->circuitBreaker?->recordFailure($tenant);
        }

        $this->logFailure($tenant, $credentials, $response);

        return $this->fallback($tenant, $credentials, $cached, $response->errorCode);
    }

    /**
     * Heartbeat. Gehört in den Scheduler, nie in den Request-Pfad.
     *
     * Technisch derselbe Endpunkt wie `refresh()` — der Server hat keinen
     * separaten Ping. Der Rückgabewert sagt, ob der Server geantwortet hat;
     * ein Fehlschlag ist kein Fehlerzustand des Dienstes, nur ein Event.
     */
    public function ping(string $tenant): bool
    {
        $status = $this->refresh($tenant);

        return $status->source === StatusSource::Live;
    }

    /**
     * Streuung für den Heartbeat, damit nicht alle Mandanten gleichzeitig
     * anklopfen. Deterministisch je Mandant: derselbe Mandant bekommt immer
     * denselben Versatz, sonst würde jeder Scheduler-Lauf neu würfeln.
     */
    public function pingOffsetFor(string $tenant): int
    {
        $hash = hexdec(substr(hash('sha256', $tenant), 0, 8));

        return (int) ($hash % max(1, $this->config->pingInterval));
    }

    /**
     * Ist für diesen Mandanten ein Heartbeat fällig?
     */
    public function isPingDue(string $tenant): bool
    {
        $cached = $this->cache->get($tenant);
        if ($cached === null) {
            return true;
        }

        return $cached->ageInSeconds($this->clock->now()) >= $this->config->pingInterval;
    }

    private function callWithRetries(string $tenant, TenantCredentials $credentials): TransportResponse
    {
        $params = [
            'key' => $credentials->key,
            'domain' => $this->config->serviceName,
            'db_uuid' => $credentials->instanceId !== ''
                ? $credentials->instanceId
                : $this->config->instanceIdFor($tenant),
            'module_version' => $this->config->version,
        ];

        $attempts = $this->retryPolicy->maxAttempts();
        $response = TransportResponse::transportFailure('kein Versuch ausgeführt');

        for ($attempt = 1; $attempt <= $attempts; $attempt++) {
            $response = $this->transport->call(
                '/api/license/check',
                $params,
                $credentials->key,
                $credentials->secret,
            );

            if ($response->ok || ! $response->retryable) {
                return $response;
            }

            if ($attempt < $attempts) {
                usleep($this->retryPolicy->delayMicrosecondsFor($attempt));
            }
        }

        return $response;
    }

    /**
     * Was gilt, wenn der Server nicht verwertbar geantwortet hat.
     */
    private function fallback(
        string $tenant,
        TenantCredentials $credentials,
        ?LicenseStatus $cached,
        ?string $reason,
    ): LicenseStatus {
        $now = $this->clock->now();

        if ($cached !== null) {
            $age = $cached->ageInSeconds($now);

            // Cache noch frisch: kein Grund für Alarm, der Stand ist aktuell genug.
            if ($age < $this->config->cacheTtl) {
                return $cached->withSource(StatusSource::Cache);
            }

            if ($age < $this->config->localGrace) {
                $this->events->dispatch(new LicenseServerUnreachable(
                    $tenant,
                    max(0, $this->config->localGrace - $age),
                    $reason,
                ));

                return $cached->withSource(StatusSource::LocalGrace);
            }

            $this->events->dispatch(new LicenseLocalGraceExpired($tenant, $age));
            $this->logger->error(
                '[wb-license] Local Grace für {tenant} abgelaufen — letzter Stand {age}s alt',
                ['tenant' => $tenant, 'age' => $age, 'key' => $credentials->maskedKey()],
            );

            return LicenseStatus::unknown($tenant, $credentials->key, $cached->checkedAt);
        }

        // Nie eine Antwort gehabt. Das ist der Zustand beim allerersten Start
        // mit unerreichbarem Server — und der gefährlichste, weil es nichts
        // gibt, worauf man zurückfallen könnte.
        $this->events->dispatch(new LicenseLocalGraceExpired($tenant, PHP_INT_MAX));

        return LicenseStatus::unknown($tenant, $credentials->key, $now);
    }

    private function emitTransitions(?LicenseStatus $previous, LicenseStatus $current): void
    {
        $previousState = $previous?->state ?? LicenseState::Unknown;
        if ($previousState === $current->state) {
            return;
        }

        $this->events->dispatch(new LicenseStateChanged($current->tenant, $previousState, $current->state));

        // Nur beim Übergang IN die Grace-Period, nicht bei jedem Ping
        // währenddessen — sonst alarmiert der Dienst alle sechs Stunden neu.
        if ($current->state === LicenseState::Grace) {
            $this->events->dispatch(new LicenseEnteredGrace($current->tenant, $current->graceUntil));
        }
    }

    private function logFailure(string $tenant, TenantCredentials $credentials, TransportResponse $response): void
    {
        $context = [
            'tenant' => $tenant,
            'key' => $credentials->maskedKey(),
            'error' => $response->errorCode,
            'message' => $response->message,
        ];

        // Konfigurationsfehler laut, Ausfälle leiser: ein unbekannter Key
        // oder eine kaputte Signatur wird durch Warten nicht besser und
        // braucht einen Menschen.
        if ($response->errorCode === 'KEY_NOT_FOUND') {
            $this->logger->error(
                '[wb-license] Server kennt den Schlüssel für {tenant} nicht ({key}) — '
                . 'im Odoo angelegt und dem richtigen Mandanten zugeordnet?',
                $context,
            );

            return;
        }

        if ($response->isSignatureProblem()) {
            $this->logger->error(
                '[wb-license] Signatur für {tenant} abgelehnt ({error}) — Secret korrekt '
                . 'hinterlegt? Systemzeit korrekt? Der Server toleriert 300s Drift.',
                $context,
            );

            return;
        }

        $this->logger->warning('[wb-license] Lizenzabfrage für {tenant} fehlgeschlagen: {error}', $context);
    }
}
