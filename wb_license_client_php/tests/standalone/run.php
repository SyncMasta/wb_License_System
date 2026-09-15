<?php

declare(strict_types=1);

/**
 * Offline-Selbsttest: die Pflicht-Szenarien aus dem Übergabe-Dokument (§11),
 * lauffähig ohne Composer und ohne Netzwerk.
 *
 *     php tests/standalone/run.php
 *
 * Exit-Code 0 = alles grün.
 */

require __DIR__ . '/bootstrap.php';

use WissenBeratung\LicenseClient\Cache\InMemoryStatusCache;
use WissenBeratung\LicenseClient\Config\LicenseConfig;
use WissenBeratung\LicenseClient\Dto\LicenseStatus;
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

const TENANT = 'umantis-kunde-a';
const KEY = 'WB-UMAN-1a2b3c4dQF';
const SECRET = 'WBS-TESTSECRETTESTSECRETTESTSECRETTESTSECRET';

/**
 * Baut einen Client mit stellbarer Zeit und gefaktem Transport.
 *
 * @return array{0: LicenseClient, 1: FakeTransport, 2: FrozenClock, 3: RecordingDispatcher, 4: InMemoryStatusCache, 5: RecordingLogger}
 */
function makeClient(?LicenseConfig $config = null, ?FrozenClock $clock = null, bool $withCircuit = true): array
{
    $clock ??= new FrozenClock();
    $config ??= new LicenseConfig(retries: 0, serviceInstanceId: 'test-instance');
    $transport = new FakeTransport();
    $events = new RecordingDispatcher();
    $cache = new InMemoryStatusCache();
    $logger = new RecordingLogger();

    $breaker = $withCircuit
        ? new CircuitBreaker(new ArrayCache($clock), $clock, threshold: 2, cooldownSeconds: 300)
        : null;

    $client = new LicenseClient(
        config: $config,
        transport: $transport,
        resolver: new ArrayTenantKeyResolver([TENANT => ['key' => KEY, 'secret' => SECRET]]),
        cache: $cache,
        clock: $clock,
        circuitBreaker: $breaker,
        events: $events,
        logger: $logger,
    );

    return [$client, $transport, $clock, $events, $cache, $logger];
}

/** @param array<string, mixed> $overrides */
function serverOk(string $state = 'active', array $overrides = []): TransportResponse
{
    return TransportResponse::success(array_merge([
        'state' => $state,
        'valid_from' => '2026-01-01',
        'valid_to' => '2026-12-31',
        'grace_until' => null,
        'product_code' => 'UMAN',
    ], $overrides));
}

$t = new TestRunner();

// ---------------------------------------------------------------- Szenarien

$t->test('Antwort active → Process, Cache gesetzt, source = Live', function (TestRunner $t): void {
    [$client, $transport, , $events, $cache] = makeClient();
    $transport->queue(serverOk('active'));

    $status = $client->refresh(TENANT);

    $t->same(LicenseState::Active, $status->state, 'State');
    $t->same(StatusSource::Live, $status->source, 'Quelle');
    $t->assert($cache->get(TENANT) !== null, 'Cache wurde nicht gesetzt');

    $gate = new EntitlementGate(new LicenseConfig(enforcement: EnforcementMode::Enforce));
    $t->same(Decision::Process, $gate->decide($status), 'Entscheidung');
    $t->same(0, $events->countOf(LicenseBlocked::class), 'kein Blocked-Event erwartet');
});

$t->test('Antwort grace → ProcessWithWarning, Event EnteredGrace genau einmal', function (TestRunner $t): void {
    [$client, $transport, , $events] = makeClient();
    $transport->queue(
        serverOk('grace', ['grace_until' => '2026-10-01']),
        serverOk('grace', ['grace_until' => '2026-10-02']),
    );

    $first = $client->refresh(TENANT);
    $client->refresh(TENANT);

    $t->same(LicenseState::Grace, $first->state, 'State');
    $t->assert($first->graceUntil?->format('Y-m-d') === '2026-10-01', 'graceUntil nicht geparst');
    $t->same(1, $events->countOf(LicenseEnteredGrace::class), 'EnteredGrace-Events');

    $gate = new EntitlementGate(new LicenseConfig(enforcement: EnforcementMode::Enforce));
    $t->same(Decision::ProcessWithWarning, $gate->decide($first), 'Entscheidung');
});

$t->test('Antwort revoked bei enforce → Hold, Event Blocked', function (TestRunner $t): void {
    [$client, $transport] = makeClient();
    $transport->queue(serverOk('revoked'));
    $status = $client->refresh(TENANT);

    $events = new RecordingDispatcher();
    $gate = new EntitlementGate(new LicenseConfig(enforcement: EnforcementMode::Enforce), $events);

    $t->same(Decision::Hold, $gate->decide($status), 'Entscheidung');
    $t->same(1, $events->countOf(LicenseBlocked::class), 'Blocked-Events');
});

$t->test('Antwort revoked bei warn → Process, Event Blocked trotzdem', function (TestRunner $t): void {
    [$client, $transport] = makeClient();
    $transport->queue(serverOk('revoked'));
    $status = $client->refresh(TENANT);

    $events = new RecordingDispatcher();
    $gate = new EntitlementGate(new LicenseConfig(enforcement: EnforcementMode::Warn), $events);

    $t->same(Decision::Process, $gate->decide($status), 'Entscheidung');
    $t->same(1, $events->countOf(LicenseBlocked::class), 'Blocked-Event muss auch im Warn-Modus kommen');
});

$t->test('Server-Timeout, Cache frisch → source = Cache, keine Exception', function (TestRunner $t): void {
    [$client, $transport, $clock, $events] = makeClient();
    $transport->queue(serverOk('active'));
    $client->refresh(TENANT);

    $clock->advance(60);
    $transport->always(TransportResponse::transportFailure('cURL timeout'));
    $status = $client->refresh(TENANT);

    $t->same(StatusSource::Cache, $status->source, 'Quelle');
    $t->same(LicenseState::Active, $status->state, 'State');
    $t->same(0, $events->countOf(LicenseServerUnreachable::class), 'bei frischem Cache kein Alarm');
});

$t->test('Timeout, Cache älter als TTL, innerhalb Local Grace → LocalGrace + Event', function (TestRunner $t): void {
    [$client, $transport, $clock, $events] = makeClient(new LicenseConfig(cacheTtl: 900, localGrace: 259200, retries: 0));
    $transport->queue(serverOk('active'));
    $client->refresh(TENANT);

    $clock->advance(3600);
    $transport->always(TransportResponse::transportFailure('cURL timeout'));
    $status = $client->refresh(TENANT);

    $t->same(StatusSource::LocalGrace, $status->source, 'Quelle');
    $t->same(LicenseState::Active, $status->state, 'State bleibt der letzte bekannte');
    $t->same(1, $events->countOf(LicenseServerUnreachable::class), 'Unreachable-Events');

    $event = $events->ofType(LicenseServerUnreachable::class)[0];
    $t->assert($event->remainingGraceSeconds > 0, 'Restlaufzeit muss positiv sein');
});

$t->test('Timeout, Local Grace überschritten → unknown + Event, Entscheidung je Modus', function (TestRunner $t): void {
    [$client, $transport, $clock, $events] = makeClient(new LicenseConfig(cacheTtl: 900, localGrace: 3600, retries: 0));
    $transport->queue(serverOk('active'));
    $client->refresh(TENANT);

    $clock->advance(7200);
    $transport->always(TransportResponse::transportFailure('cURL timeout'));
    $status = $client->refresh(TENANT);

    $t->same(LicenseState::Unknown, $status->state, 'State');
    $t->same(1, $events->countOf(LicenseLocalGraceExpired::class), 'LocalGraceExpired-Events');

    $enforce = new EntitlementGate(new LicenseConfig(enforcement: EnforcementMode::Enforce));
    $warn = new EntitlementGate(new LicenseConfig(enforcement: EnforcementMode::Warn));
    $off = new EntitlementGate(new LicenseConfig(enforcement: EnforcementMode::Off));

    $t->same(Decision::Hold, $enforce->decide($status), 'enforce');
    $t->same(Decision::Process, $warn->decide($status), 'warn');
    $t->same(Decision::Process, $off->decide($status), 'off');
});

$t->test('TOO_MANY_REQUESTS → kein sofortiger Retry, Circuit gesperrt', function (TestRunner $t): void {
    [$client, $transport, $clock] = makeClient(new LicenseConfig(retries: 2, rateLimitCooldown: 900));
    $transport->always(TransportResponse::apiError('TOO_MANY_REQUESTS'));

    $client->refresh(TENANT);
    $t->same(1, $transport->calls, 'Rate-Limit darf nicht wiederholt werden');

    // Zweiter Aufruf innerhalb der Sperre geht gar nicht erst raus.
    $client->refresh(TENANT);
    $t->same(1, $transport->calls, 'Sperre wurde nicht beachtet');

    $clock->advance(901);
    $transport->always(serverOk('active'));
    $client->refresh(TENANT);
    $t->same(2, $transport->calls, 'nach Ablauf der Sperre muss wieder gefragt werden');
});

$t->test('SIGNATURE_INVALID → kein Retry, unknown, deutlicher Log-Eintrag', function (TestRunner $t): void {
    [$client, $transport, , , , $logger] = makeClient(new LicenseConfig(retries: 2));
    $transport->always(TransportResponse::apiError('SIGNATURE_INVALID'));

    $status = $client->refresh(TENANT);

    $t->same(1, $transport->calls, 'Signaturfehler wird durch Retry nicht besser');
    $t->same(LicenseState::Unknown, $status->state, 'State');
    $t->assert(str_contains($logger->dump(), 'Signatur'), 'Log muss die Ursache benennen');
});

$t->test('KEY_NOT_FOUND → kein Retry, unknown, deutlicher Log-Eintrag', function (TestRunner $t): void {
    [$client, $transport, , , , $logger] = makeClient(new LicenseConfig(retries: 2));
    $transport->always(TransportResponse::apiError('KEY_NOT_FOUND'));

    $status = $client->refresh(TENANT);

    $t->same(1, $transport->calls, 'unbekannter Key wird durch Retry nicht bekannter');
    $t->same(LicenseState::Unknown, $status->state, 'State');
    $t->assert(str_contains($logger->dump(), 'Schlüssel'), 'Log muss die Ursache benennen');
});

$t->test('Serverfehler → zwei Retries, dann Cache oder Local Grace', function (TestRunner $t): void {
    [$client, $transport, $clock] = makeClient(new LicenseConfig(retries: 2, retryBaseDelayMs: 1, cacheTtl: 900, localGrace: 259200));
    $transport->queue(serverOk('active'));
    $client->refresh(TENANT);
    $transport->calls = 0;

    $clock->advance(3600);
    $transport->always(TransportResponse::serverFault('Odoo Server Error'));
    $status = $client->refresh(TENANT);

    $t->same(3, $transport->calls, 'ein Versuch plus zwei Retries');
    $t->same(StatusSource::LocalGrace, $status->source, 'Quelle');
});

$t->test('Circuit offen → kein HTTP-Aufruf, direkt Local Grace', function (TestRunner $t): void {
    [$client, $transport, $clock] = makeClient(new LicenseConfig(cacheTtl: 900, localGrace: 259200, retries: 0));
    $transport->queue(serverOk('active'));
    $client->refresh(TENANT);

    $clock->advance(3600);
    $transport->always(TransportResponse::transportFailure('connection refused'));
    $client->refresh(TENANT);
    $client->refresh(TENANT);
    $callsBefore = $transport->calls;

    $status = $client->refresh(TENANT);

    $t->same($callsBefore, $transport->calls, 'bei offenem Circuit darf kein Call rausgehen');
    $t->same(StatusSource::LocalGrace, $status->source, 'Quelle');
});

$t->test('Statuswechsel active → grace → active: je genau ein StateChanged', function (TestRunner $t): void {
    [$client, $transport, $clock, $events] = makeClient();
    $transport->queue(
        serverOk('active'),
        serverOk('active'),
        serverOk('grace', ['grace_until' => '2026-10-01']),
        serverOk('active'),
    );

    foreach ([0, 10, 20, 30] as $offset) {
        $clock->advance($offset === 0 ? 0 : 10);
        $client->refresh(TENANT);
    }

    // unknown→active, active→grace, grace→active
    $t->same(3, $events->countOf(LicenseStateChanged::class), 'StateChanged-Events');
    $t->same(1, $events->countOf(LicenseEnteredGrace::class), 'EnteredGrace nur beim Eintritt');
});

$t->test('Mehrere Mandanten: Cache-Keys und Circuit sauber getrennt', function (TestRunner $t): void {
    $clock = new FrozenClock();
    $config = new LicenseConfig(cacheTtl: 900, localGrace: 259200, retries: 0);
    $transport = new FakeTransport();
    $cache = new InMemoryStatusCache();
    $breaker = new CircuitBreaker(new ArrayCache($clock), $clock, threshold: 1, cooldownSeconds: 300);

    $client = new LicenseClient(
        config: $config,
        transport: $transport,
        resolver: new ArrayTenantKeyResolver([
            'kunde-a' => ['key' => 'WB-UMAN-1a2b3c4dQF', 'secret' => SECRET],
            'kunde-b' => ['key' => 'WB-UMAN-99887766ZZ', 'secret' => SECRET],
        ]),
        cache: $cache,
        clock: $clock,
        circuitBreaker: $breaker,
    );

    $transport->queue(serverOk('active'), serverOk('revoked'));
    $a = $client->refresh('kunde-a');
    $b = $client->refresh('kunde-b');

    $t->same(LicenseState::Active, $a->state, 'Mandant A');
    $t->same(LicenseState::Revoked, $b->state, 'Mandant B');
    $t->assert($cache->get('kunde-a')?->state === LicenseState::Active, 'Cache A überschrieben');
    $t->assert($cache->get('kunde-b')?->state === LicenseState::Revoked, 'Cache B überschrieben');

    // Mandant B in den Circuit fahren; A muss weiter fragen dürfen.
    $clock->advance(1000);
    $transport->always(TransportResponse::transportFailure('timeout'));
    $client->refresh('kunde-b');
    $t->assert($breaker->isOpen('kunde-b'), 'Circuit B müsste offen sein');
    $t->assert(! $breaker->isOpen('kunde-a'), 'Circuit A darf nicht mitgerissen werden');
});

$t->test('Maskierung: Schlüssel erscheint in keinem Log vollständig', function (TestRunner $t): void {
    [$client, $transport, , , , $logger] = makeClient();
    $transport->always(TransportResponse::apiError('KEY_NOT_FOUND'));
    $client->refresh(TENANT);

    $dump = $logger->dump();
    $t->assert(! str_contains($dump, KEY), 'vollständiger Key im Log gefunden');
    $t->assert(! str_contains($dump, SECRET), 'Secret im Log gefunden');
    $t->assert(str_contains($dump, Signature::mask(KEY)), 'maskierter Key fehlt im Log');
});

// ------------------------------------------------- Zusätzliche Absicherungen

$t->test('status() nutzt den Cache und fragt den Server nicht', function (TestRunner $t): void {
    [$client, $transport, $clock] = makeClient(new LicenseConfig(cacheTtl: 900, retries: 0));
    $transport->queue(serverOk('active'));
    $client->refresh(TENANT);

    $clock->advance(60);
    $status = $client->status(TENANT);

    $t->same(1, $transport->calls, 'status() darf bei frischem Cache nicht anfragen');
    $t->same(StatusSource::Cache, $status->source, 'Quelle');
});

$t->test('Requests werden signiert, wenn ein Secret hinterlegt ist', function (TestRunner $t): void {
    [$client, $transport] = makeClient();
    $transport->always(serverOk('active'));
    $client->refresh(TENANT);

    $t->same(KEY, $transport->received[0]['key'], 'Key');
    $t->same(SECRET, $transport->received[0]['secret'], 'Secret');
    $t->assert(($transport->received[0]['params']['db_uuid'] ?? '') !== '', 'db_uuid fehlt');
});

$t->test('Instanzkennung ist je Mandant verschieden und stabil', function (TestRunner $t): void {
    $config = new LicenseConfig(serviceInstanceId: 'instanz-1');
    $t->same($config->instanceIdFor('kunde-a'), $config->instanceIdFor('kunde-a'), 'stabil');
    $t->assert($config->instanceIdFor('kunde-a') !== $config->instanceIdFor('kunde-b'), 'nicht mandantenspezifisch');
    $t->same(64, strlen($config->instanceIdFor('kunde-a')), 'Länge');
});

$t->test('Unbekannter Mandant → unknown statt Exception', function (TestRunner $t): void {
    [$client] = makeClient();
    $status = $client->refresh('gibt-es-nicht');
    $t->same(LicenseState::Unknown, $status->state, 'State');
});

$t->test('Unbekannter Server-State → unknown statt Fatal', function (TestRunner $t): void {
    [$client, $transport] = makeClient();
    $transport->always(serverOk('irgendwas_neues'));
    $status = $client->refresh(TENANT);
    $t->same(LicenseState::Unknown, $status->state, 'State');
});

$t->test('Cache-Serialisierung überlebt den Roundtrip', function (TestRunner $t): void {
    $status = LicenseStatus::fromServerPayload(
        TENANT,
        KEY,
        ['state' => 'grace', 'valid_from' => '2026-01-01', 'valid_to' => '2026-12-31', 'grace_until' => '2026-10-01'],
        new DateTimeImmutable('@1789000000'),
    );

    $restored = LicenseStatus::fromArray($status->toArray());

    $t->assert($restored !== null, 'Roundtrip lieferte null');
    $t->same(LicenseState::Grace, $restored->state, 'State');
    $t->same('2026-10-01', $restored->graceUntil?->format('Y-m-d'), 'graceUntil');
    $t->same(1789000000, $restored->checkedAt->getTimestamp(), 'checkedAt');
});

$t->test('Kaputter Cache-Eintrag führt zu null, nicht zum Fatal', function (TestRunner $t): void {
    $t->assert(LicenseStatus::fromArray(['kaputt' => true]) === null, 'unvollständig');
    $t->assert(LicenseStatus::fromArray('kein array') === null, 'falscher Typ');
    $t->assert(LicenseStatus::fromArray(['tenant' => 'x', 'state' => 'quatsch', 'checked_at' => 1]) === null, 'unbekannter State');
});

$t->test('Heartbeat-Versatz ist deterministisch und im Intervall', function (TestRunner $t): void {
    [$client] = makeClient(new LicenseConfig(pingInterval: 21600));
    $offset = $client->pingOffsetFor(TENANT);
    $t->same($offset, $client->pingOffsetFor(TENANT), 'nicht deterministisch');
    $t->assert($offset >= 0 && $offset < 21600, 'Versatz außerhalb des Intervalls');
    $t->assert($client->pingOffsetFor('anderer-mandant') !== $offset, 'Mandanten teilen sich den Versatz');
});

$t->test('Gate erklärt jeden State in einem Satz', function (TestRunner $t): void {
    $gate = new EntitlementGate(new LicenseConfig());
    foreach (LicenseState::cases() as $state) {
        $status = new LicenseStatus(
            tenant: TENANT, key: KEY, state: $state,
            source: StatusSource::Live, checkedAt: new DateTimeImmutable('@1789000000'),
        );
        $t->assert(strlen($gate->explain($status)) > 10, 'kein Text für ' . $state->value);
    }
});

exit($t->summary());
