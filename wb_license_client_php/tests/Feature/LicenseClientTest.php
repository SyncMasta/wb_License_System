<?php

declare(strict_types=1);

use WissenBeratung\LicenseClient\Cache\InMemoryStatusCache;
use WissenBeratung\LicenseClient\Config\LicenseConfig;
use WissenBeratung\LicenseClient\EntitlementGate;
use WissenBeratung\LicenseClient\Enum\Decision;
use WissenBeratung\LicenseClient\Enum\EnforcementMode;
use WissenBeratung\LicenseClient\Enum\LicenseState;
use WissenBeratung\LicenseClient\Enum\StatusSource;
use WissenBeratung\LicenseClient\Event\LicenseBlocked;
use WissenBeratung\LicenseClient\Event\LicenseEnteredGrace;
use WissenBeratung\LicenseClient\Event\LicenseLocalGraceExpired;
use WissenBeratung\LicenseClient\Event\LicenseServerUnreachable;
use WissenBeratung\LicenseClient\Event\LicenseStateChanged;
use WissenBeratung\LicenseClient\Http\Signature;
use WissenBeratung\LicenseClient\Http\TransportResponse;
use WissenBeratung\LicenseClient\LicenseClient;
use WissenBeratung\LicenseClient\Resilience\CircuitBreaker;
use WissenBeratung\LicenseClient\Tenant\ArrayTenantKeyResolver;
use WissenBeratung\LicenseClient\Tests\Support\ArrayCache;
use WissenBeratung\LicenseClient\Tests\Support\FakeTransport;
use WissenBeratung\LicenseClient\Tests\Support\FrozenClock;
use WissenBeratung\LicenseClient\Tests\Support\RecordingDispatcher;
use WissenBeratung\LicenseClient\Tests\Support\RecordingLogger;

/**
 * Die Pflicht-Szenarien aus §11 des Übergabe-Dokuments.
 *
 * Alle gegen Test-Doubles, keine echten Aufrufe — der Kern wirft keine
 * Exceptions in den Aufrufpfad, das lässt sich nur so prüfen.
 */

const T = 'umantis-kunde-a';
const K = 'WB-UMAN-1a2b3c4dQF';
const S = 'WBS-TESTSECRETTESTSECRETTESTSECRETTESTSECRET';

/**
 * @return array{client: LicenseClient, transport: FakeTransport, clock: FrozenClock,
 *               events: RecordingDispatcher, cache: InMemoryStatusCache,
 *               logger: RecordingLogger, breaker: CircuitBreaker}
 */
function harness(?LicenseConfig $config = null, int $circuitThreshold = 2): array
{
    $clock = new FrozenClock();
    $config ??= new LicenseConfig(retries: 0, serviceInstanceId: 'test-instance');
    $transport = new FakeTransport();
    $events = new RecordingDispatcher();
    $cache = new InMemoryStatusCache();
    $logger = new RecordingLogger();
    $breaker = new CircuitBreaker(new ArrayCache($clock), $clock, $circuitThreshold, 300);

    return [
        'client' => new LicenseClient(
            config: $config,
            transport: $transport,
            resolver: new ArrayTenantKeyResolver([T => ['key' => K, 'secret' => S]]),
            cache: $cache,
            clock: $clock,
            circuitBreaker: $breaker,
            events: $events,
            logger: $logger,
        ),
        'transport' => $transport,
        'clock' => $clock,
        'events' => $events,
        'cache' => $cache,
        'logger' => $logger,
        'breaker' => $breaker,
    ];
}

/** @param array<string, mixed> $overrides */
function ok(string $state = 'active', array $overrides = []): TransportResponse
{
    return TransportResponse::success(array_merge([
        'state' => $state,
        'valid_from' => '2026-01-01',
        'valid_to' => '2026-12-31',
        'grace_until' => null,
        'product_code' => 'UMAN',
    ], $overrides));
}

function gate(EnforcementMode $mode, ?RecordingDispatcher $events = null): EntitlementGate
{
    return new EntitlementGate(new LicenseConfig(enforcement: $mode), $events ?? new RecordingDispatcher());
}

it('liefert bei active Process, setzt den Cache und meldet source Live', function (): void {
    $h = harness();
    $h['transport']->queue(ok('active'));

    $status = $h['client']->refresh(T);

    expect($status->state)->toBe(LicenseState::Active)
        ->and($status->source)->toBe(StatusSource::Live)
        ->and($h['cache']->get(T))->not->toBeNull()
        ->and(gate(EnforcementMode::Enforce)->decide($status))->toBe(Decision::Process);
});

it('meldet bei grace genau einmal LicenseEnteredGrace', function (): void {
    $h = harness();
    $h['transport']->queue(
        ok('grace', ['grace_until' => '2026-10-01']),
        ok('grace', ['grace_until' => '2026-10-02']),
    );

    $first = $h['client']->refresh(T);
    $h['client']->refresh(T);

    expect($first->state)->toBe(LicenseState::Grace)
        ->and($first->graceUntil?->format('Y-m-d'))->toBe('2026-10-01')
        ->and($h['events']->countOf(LicenseEnteredGrace::class))->toBe(1)
        ->and(gate(EnforcementMode::Enforce)->decide($first))->toBe(Decision::ProcessWithWarning);
});

it('hält bei revoked im Modus enforce an und meldet LicenseBlocked', function (): void {
    $h = harness();
    $h['transport']->queue(ok('revoked'));
    $status = $h['client']->refresh(T);

    $events = new RecordingDispatcher();

    expect(gate(EnforcementMode::Enforce, $events)->decide($status))->toBe(Decision::Hold)
        ->and($events->countOf(LicenseBlocked::class))->toBe(1);
});

it('verarbeitet bei revoked im Modus warn weiter, meldet aber trotzdem', function (): void {
    $h = harness();
    $h['transport']->queue(ok('revoked'));
    $status = $h['client']->refresh(T);

    $events = new RecordingDispatcher();

    expect(gate(EnforcementMode::Warn, $events)->decide($status))->toBe(Decision::Process)
        ->and($events->countOf(LicenseBlocked::class))->toBe(1);
});

it('fällt bei Server-Timeout auf den frischen Cache zurück, ohne Alarm', function (): void {
    $h = harness();
    $h['transport']->queue(ok('active'));
    $h['client']->refresh(T);

    $h['clock']->advance(60);
    $h['transport']->always(TransportResponse::transportFailure('cURL timeout'));
    $status = $h['client']->refresh(T);

    expect($status->source)->toBe(StatusSource::Cache)
        ->and($status->state)->toBe(LicenseState::Active)
        ->and($h['events']->countOf(LicenseServerUnreachable::class))->toBe(0);
});

it('bedient aus Local Grace, wenn der Cache abgelaufen und der Server weg ist', function (): void {
    $h = harness(new LicenseConfig(cacheTtl: 900, localGrace: 259200, retries: 0));
    $h['transport']->queue(ok('active'));
    $h['client']->refresh(T);

    $h['clock']->advance(3600);
    $h['transport']->always(TransportResponse::transportFailure('cURL timeout'));
    $status = $h['client']->refresh(T);

    expect($status->source)->toBe(StatusSource::LocalGrace)
        ->and($status->state)->toBe(LicenseState::Active)
        ->and($h['events']->countOf(LicenseServerUnreachable::class))->toBe(1);

    /** @var LicenseServerUnreachable $event */
    $event = $h['events']->ofType(LicenseServerUnreachable::class)[0];
    expect($event->remainingGraceSeconds)->toBeGreaterThan(0);
});

it('wird nach Ablauf der Local Grace unknown und schlägt Alarm', function (): void {
    $h = harness(new LicenseConfig(cacheTtl: 900, localGrace: 3600, retries: 0));
    $h['transport']->queue(ok('active'));
    $h['client']->refresh(T);

    $h['clock']->advance(7200);
    $h['transport']->always(TransportResponse::transportFailure('cURL timeout'));
    $status = $h['client']->refresh(T);

    expect($status->state)->toBe(LicenseState::Unknown)
        ->and($h['events']->countOf(LicenseLocalGraceExpired::class))->toBe(1)
        ->and(gate(EnforcementMode::Enforce)->decide($status))->toBe(Decision::Hold)
        ->and(gate(EnforcementMode::Warn)->decide($status))->toBe(Decision::Process)
        ->and(gate(EnforcementMode::Off)->decide($status))->toBe(Decision::Process);
});

it('rennt nicht gegen das Rate-Limit an', function (): void {
    $h = harness(new LicenseConfig(retries: 2, rateLimitCooldown: 900));
    $h['transport']->always(TransportResponse::apiError('TOO_MANY_REQUESTS'));

    $h['client']->refresh(T);
    expect($h['transport']->calls)->toBe(1);

    $h['client']->refresh(T);
    expect($h['transport']->calls)->toBe(1, 'Sperre wurde nicht beachtet');

    $h['clock']->advance(901);
    $h['transport']->always(ok('active'));
    $h['client']->refresh(T);
    expect($h['transport']->calls)->toBe(2);
});

it('wiederholt Signatur- und Key-Fehler nicht und benennt sie im Log', function (string $error, string $needle): void {
    $h = harness(new LicenseConfig(retries: 2));
    $h['transport']->always(TransportResponse::apiError($error));

    $status = $h['client']->refresh(T);

    expect($h['transport']->calls)->toBe(1)
        ->and($status->state)->toBe(LicenseState::Unknown)
        ->and($h['logger']->dump())->toContain($needle);
})->with([
    ['SIGNATURE_INVALID', 'Signatur'],
    ['SIGNATURE_REQUIRED', 'Signatur'],
    ['KEY_NOT_FOUND', 'Schlüssel'],
]);

it('wiederholt Serverfehler und fällt danach auf Local Grace zurück', function (): void {
    $h = harness(new LicenseConfig(retries: 2, retryBaseDelayMs: 1, cacheTtl: 900, localGrace: 259200));
    $h['transport']->queue(ok('active'));
    $h['client']->refresh(T);
    $h['transport']->calls = 0;

    $h['clock']->advance(3600);
    $h['transport']->always(TransportResponse::serverFault('Odoo Server Error'));
    $status = $h['client']->refresh(T);

    expect($h['transport']->calls)->toBe(3)
        ->and($status->source)->toBe(StatusSource::LocalGrace);
});

it('fragt bei offenem Circuit gar nicht erst an', function (): void {
    $h = harness(new LicenseConfig(cacheTtl: 900, localGrace: 259200, retries: 0));
    $h['transport']->queue(ok('active'));
    $h['client']->refresh(T);

    $h['clock']->advance(3600);
    $h['transport']->always(TransportResponse::transportFailure('connection refused'));
    $h['client']->refresh(T);
    $h['client']->refresh(T);
    $before = $h['transport']->calls;

    $status = $h['client']->refresh(T);

    expect($h['transport']->calls)->toBe($before)
        ->and($status->source)->toBe(StatusSource::LocalGrace);
});

it('meldet jeden Statuswechsel genau einmal', function (): void {
    $h = harness();
    $h['transport']->queue(ok('active'), ok('active'), ok('grace', ['grace_until' => '2026-10-01']), ok('active'));

    foreach ([0, 10, 10, 10] as $step) {
        $h['clock']->advance($step);
        $h['client']->refresh(T);
    }

    expect($h['events']->countOf(LicenseStateChanged::class))->toBe(3)
        ->and($h['events']->countOf(LicenseEnteredGrace::class))->toBe(1);
});

it('hält Cache und Circuit je Mandant getrennt', function (): void {
    $clock = new FrozenClock();
    $transport = new FakeTransport();
    $cache = new InMemoryStatusCache();
    $breaker = new CircuitBreaker(new ArrayCache($clock), $clock, 1, 300);

    $client = new LicenseClient(
        config: new LicenseConfig(cacheTtl: 900, localGrace: 259200, retries: 0),
        transport: $transport,
        resolver: new ArrayTenantKeyResolver([
            'kunde-a' => ['key' => K, 'secret' => S],
            'kunde-b' => ['key' => 'WB-UMAN-99887766ZZ', 'secret' => S],
        ]),
        cache: $cache,
        clock: $clock,
        circuitBreaker: $breaker,
    );

    $transport->queue(ok('active'), ok('revoked'));
    $client->refresh('kunde-a');
    $client->refresh('kunde-b');

    expect($cache->get('kunde-a')?->state)->toBe(LicenseState::Active)
        ->and($cache->get('kunde-b')?->state)->toBe(LicenseState::Revoked);

    $clock->advance(1000);
    $transport->always(TransportResponse::transportFailure('timeout'));
    $client->refresh('kunde-b');

    expect($breaker->isOpen('kunde-b'))->toBeTrue()
        ->and($breaker->isOpen('kunde-a'))->toBeFalse();
});

it('schreibt weder Schlüssel noch Secret vollständig ins Log', function (): void {
    $h = harness();
    $h['transport']->always(TransportResponse::apiError('KEY_NOT_FOUND'));
    $h['client']->refresh(T);

    $dump = $h['logger']->dump();

    expect($dump)->not->toContain(K)
        ->and($dump)->not->toContain(S)
        ->and($dump)->toContain(Signature::mask(K));
});

it('nutzt bei status() den frischen Cache statt den Server zu fragen', function (): void {
    $h = harness(new LicenseConfig(cacheTtl: 900, retries: 0));
    $h['transport']->queue(ok('active'));
    $h['client']->refresh(T);

    $h['clock']->advance(60);
    $status = $h['client']->status(T);

    expect($h['transport']->calls)->toBe(1)
        ->and($status->source)->toBe(StatusSource::Cache);
});

it('reicht Schlüssel und Secret an den Transport durch', function (): void {
    $h = harness();
    $h['transport']->always(ok('active'));
    $h['client']->refresh(T);

    expect($h['transport']->received[0]['key'])->toBe(K)
        ->and($h['transport']->received[0]['secret'])->toBe(S)
        ->and($h['transport']->received[0]['params']['db_uuid'])->not->toBe('');
});

it('behandelt unbekannte Mandanten und unbekannte States als unknown', function (): void {
    $h = harness();
    expect($h['client']->refresh('gibt-es-nicht')->state)->toBe(LicenseState::Unknown);

    $h['transport']->always(ok('irgendein_neuer_state'));
    expect($h['client']->refresh(T)->state)->toBe(LicenseState::Unknown);
});
