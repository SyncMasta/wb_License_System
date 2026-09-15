<?php

declare(strict_types=1);

use WissenBeratung\LicenseClient\Cache\PsrStatusCache;
use WissenBeratung\LicenseClient\Clock\SystemClock;
use WissenBeratung\LicenseClient\Config\LicenseConfig;
use WissenBeratung\LicenseClient\Dto\LicenseStatus;
use WissenBeratung\LicenseClient\Dto\TenantCredentials;
use WissenBeratung\LicenseClient\EntitlementGate;
use WissenBeratung\LicenseClient\Enum\Decision;
use WissenBeratung\LicenseClient\Enum\EnforcementMode;
use WissenBeratung\LicenseClient\Enum\LicenseState;
use WissenBeratung\LicenseClient\Enum\StatusSource;
use WissenBeratung\LicenseClient\Resilience\CircuitBreaker;
use WissenBeratung\LicenseClient\Resilience\RetryPolicy;
use WissenBeratung\LicenseClient\Tenant\ArrayTenantKeyResolver;
use WissenBeratung\LicenseClient\Tenant\CallbackTenantKeyResolver;
use WissenBeratung\LicenseClient\Tests\Support\ArrayCache;
use WissenBeratung\LicenseClient\Tests\Support\FrozenClock;

/**
 * Bausteine, die im Zusammenspiel-Test nicht sichtbar werden: Enums,
 * Konfiguration, Cache-Adapter, Resolver, Circuit Breaker.
 */

// ------------------------------------------------------------------ Enums

it('bildet unbekannte Server-States auf unknown ab', function (): void {
    expect(LicenseState::fromServer('active'))->toBe(LicenseState::Active)
        ->and(LicenseState::fromServer('was_neues'))->toBe(LicenseState::Unknown)
        ->and(LicenseState::fromServer(null))->toBe(LicenseState::Unknown);
});

it('weiß, welche States einen laufenden Vertrag bedeuten', function (LicenseState $state, bool $entitled): void {
    expect($state->isEntitled())->toBe($entitled);
})->with([
    [LicenseState::Active, true],
    [LicenseState::Trial, true],
    [LicenseState::Grace, true],
    [LicenseState::Issued, true],
    [LicenseState::Expired, false],
    [LicenseState::Revoked, false],
    [LicenseState::Cancelled, false],
    [LicenseState::Unknown, false],
]);

it('liest den Enforcement-Modus tolerant aus der Konfiguration', function (): void {
    expect(EnforcementMode::fromString('enforce'))->toBe(EnforcementMode::Enforce)
        ->and(EnforcementMode::fromString('  OFF '))->toBe(EnforcementMode::Off)
        ->and(EnforcementMode::fromString('quatsch'))->toBe(EnforcementMode::Warn)
        ->and(EnforcementMode::fromString(null))->toBe(EnforcementMode::Warn);
});

it('lässt nur Hold die Verarbeitung anhalten', function (): void {
    expect(Decision::Process->allowsProcessing())->toBeTrue()
        ->and(Decision::ProcessWithWarning->allowsProcessing())->toBeTrue()
        ->and(Decision::Hold->allowsProcessing())->toBeFalse();
});

// ---------------------------------------------------------- Konfiguration

it('baut die Konfiguration aus einem Array und fällt sonst auf Defaults zurück', function (): void {
    $config = LicenseConfig::fromArray([
        'base_url' => 'https://staging.wissen-beratung.de/',
        'enforcement' => 'enforce',
        'cache_ttl' => 60,
        'retries' => -3,
        'unbekannter_schluessel' => 'wird ignoriert',
    ]);

    expect($config->baseUrl)->toBe('https://staging.wissen-beratung.de')
        ->and($config->enforcement)->toBe(EnforcementMode::Enforce)
        ->and($config->cacheTtl)->toBe(60)
        ->and($config->retries)->toBe(0, 'negative Werte werden auf 0 geklemmt')
        ->and($config->localGrace)->toBe(259200, 'Default bleibt stehen');
});

it('erzwingt einen brauchbaren Circuit-Schwellwert', function (): void {
    expect(LicenseConfig::fromArray(['circuit_threshold' => 0])->circuitThreshold)->toBe(1);
});

it('baut den User-Agent nach dem vereinbarten Muster', function (): void {
    $config = new LicenseConfig(serviceName: 'wissen-api', version: '0.1.0');

    expect($config->userAgent())->toBe('wb-license-client-php/0.1.0 (wissen-api)');
});

it('erzeugt je Mandant eine eigene, stabile Instanzkennung', function (): void {
    $config = new LicenseConfig(serviceInstanceId: 'instanz-1');

    expect($config->instanceIdFor('a'))->toBe($config->instanceIdFor('a'))
        ->and($config->instanceIdFor('a'))->not->toBe($config->instanceIdFor('b'))
        ->and(strlen($config->instanceIdFor('a')))->toBe(64);

    // Andere Dienstinstanz, anderer Wert — sonst würden zwei Instanzen
    // serverseitig als dieselbe erscheinen.
    $other = new LicenseConfig(serviceInstanceId: 'instanz-2');
    expect($config->instanceIdFor('a'))->not->toBe($other->instanceIdFor('a'));
});

// ------------------------------------------------------------------- DTOs

it('serialisiert den Status verlustfrei durch den Cache', function (): void {
    $status = LicenseStatus::fromServerPayload('t', 'WB-UMAN-1a2b3c4dQF', [
        'state' => 'grace',
        'valid_from' => '2026-01-01',
        'valid_to' => '2026-12-31',
        'grace_until' => '2026-10-01',
    ], new DateTimeImmutable('@1789000000'));

    $restored = LicenseStatus::fromArray($status->toArray());

    expect($restored)->not->toBeNull()
        ->and($restored->state)->toBe(LicenseState::Grace)
        ->and($restored->validFrom?->format('Y-m-d'))->toBe('2026-01-01')
        ->and($restored->validTo?->format('Y-m-d'))->toBe('2026-12-31')
        ->and($restored->graceUntil?->format('Y-m-d'))->toBe('2026-10-01')
        ->and($restored->checkedAt->getTimestamp())->toBe(1789000000);
});

it('macht aus kaputten Cache-Einträgen null statt eines Fatals', function (mixed $input): void {
    expect(LicenseStatus::fromArray($input))->toBeNull();
})->with([
    ['kein array'],
    [['kaputt' => true]],
    [[ 'tenant' => 'x', 'state' => 'gibtsnicht', 'checked_at' => 1]],
    [null],
]);

it('ignoriert unbrauchbare Datumswerte', function (): void {
    $status = LicenseStatus::fromServerPayload('t', 'k', [
        'state' => 'active',
        'valid_to' => '',
        'grace_until' => 12345,
    ], new DateTimeImmutable('@1789000000'));

    expect($status->validTo)->toBeNull()
        ->and($status->graceUntil)->toBeNull();
});

it('rechnet das Alter des letzten Serverkontakts', function (): void {
    $status = LicenseStatus::unknown('t', 'k', new DateTimeImmutable('@1789000000'));

    expect($status->ageInSeconds(new DateTimeImmutable('@1789003600')))->toBe(3600)
        ->and($status->ageInSeconds(new DateTimeImmutable('@1788000000')))->toBe(0, 'nie negativ')
        ->and($status->source)->toBe(StatusSource::LocalGrace);
});

it('wechselt die Quelle, ohne den Rest anzufassen', function (): void {
    $status = LicenseStatus::fromServerPayload('t', 'WB-UMAN-1a2b3c4dQF', ['state' => 'active'], new DateTimeImmutable('@1789000000'));
    $moved = $status->withSource(StatusSource::LocalGrace);

    expect($moved->source)->toBe(StatusSource::LocalGrace)
        ->and($moved->state)->toBe($status->state)
        ->and($moved->checkedAt)->toEqual($status->checkedAt)
        ->and($moved->maskedKey())->toBe('WB-UMAN****4dQF');
});

it('hält das Secret aus Debug-Ausgaben heraus', function (): void {
    $credentials = new TenantCredentials('WB-UMAN-1a2b3c4dQF', 'WBS-geheim', 'inst-1');
    $debug = $credentials->__debugInfo();

    expect($credentials->hasSecret())->toBeTrue()
        ->and($debug['secret'])->toBe('***')
        ->and($debug['key'])->toBe('WB-UMAN****4dQF')
        ->and(json_encode($debug))->not->toContain('geheim');

    expect((new TenantCredentials('WB-UMAN-1a2b3c4dQF'))->hasSecret())->toBeFalse();
});

// --------------------------------------------------------------- Resolver

it('löst Mandanten aus der Konfiguration auf, auch in Kurzform', function (): void {
    $resolver = new ArrayTenantKeyResolver([
        'voll' => ['key' => 'WB-UMAN-1a2b3c4dQF', 'secret' => 'WBS-x', 'instance_id' => 'i-1'],
        'kurz' => 'WB-TELE-abcdef01GH',
        'leer' => ['secret' => 'ohne key'],
        'leerstring' => '',
    ]);

    expect($resolver->resolve('voll')?->secret)->toBe('WBS-x')
        ->and($resolver->resolve('voll')?->instanceId)->toBe('i-1')
        ->and($resolver->resolve('kurz')?->key)->toBe('WB-TELE-abcdef01GH')
        ->and($resolver->resolve('kurz')?->hasSecret())->toBeFalse()
        ->and($resolver->resolve('leer'))->toBeNull()
        ->and($resolver->resolve('leerstring'))->toBeNull()
        ->and($resolver->resolve('unbekannt'))->toBeNull();
});

it('löst Mandanten über einen Callback auf', function (): void {
    $resolver = new CallbackTenantKeyResolver(fn (string $tenant): ?array => $tenant === 'da'
        ? ['key' => 'WB-UMAN-1a2b3c4dQF', 'secret' => 'WBS-y']
        : null);

    expect($resolver->resolve('da')?->key)->toBe('WB-UMAN-1a2b3c4dQF')
        ->and($resolver->resolve('da')?->secret)->toBe('WBS-y')
        ->and($resolver->resolve('weg'))->toBeNull();
});

it('nimmt vom Callback auch fertige Credentials entgegen', function (): void {
    $resolver = new CallbackTenantKeyResolver(
        fn (): TenantCredentials => new TenantCredentials('WB-UMAN-1a2b3c4dQF')
    );

    expect($resolver->resolve('egal'))->toBeInstanceOf(TenantCredentials::class);
});

it('behandelt einen werfenden Callback als unbekannten Mandanten', function (): void {
    // Eine klemmende Mandanten-Tabelle darf die Verarbeitung nicht anhalten.
    $resolver = new CallbackTenantKeyResolver(function (): never {
        throw new RuntimeException('DB weg');
    });

    expect($resolver->resolve('egal'))->toBeNull();
});

// ------------------------------------------------------------------ Cache

it('legt Status über einen PSR-16-Cache ab und liest sie zurück', function (): void {
    $clock = new FrozenClock();
    $cache = new PsrStatusCache(new ArrayCache($clock));
    $status = LicenseStatus::fromServerPayload('t', 'k', ['state' => 'active'], $clock->now());

    expect($cache->get('t'))->toBeNull();

    $cache->put($status, 900);
    expect($cache->get('t')?->state)->toBe(LicenseState::Active);

    $cache->forget('t');
    expect($cache->get('t'))->toBeNull();
});

it('lässt abgelaufene Cache-Einträge verfallen', function (): void {
    $clock = new FrozenClock();
    $cache = new PsrStatusCache(new ArrayCache($clock));
    $cache->put(LicenseStatus::fromServerPayload('t', 'k', ['state' => 'active'], $clock->now()), 60);

    $clock->advance(61);

    expect($cache->get('t'))->toBeNull();
});

it('liefert die Systemzeit in UTC', function (): void {
    $now = (new SystemClock())->now();

    expect($now->getTimezone()->getName())->toBe('UTC')
        ->and(abs($now->getTimestamp() - time()))->toBeLessThan(5);
});

// ------------------------------------------------------------- Resilienz

it('steigert den Backoff und bleibt im Rahmen', function (): void {
    $policy = new RetryPolicy(maxRetries: 3, baseDelayMs: 100, maxDelayMs: 1000);

    expect($policy->maxAttempts())->toBe(4);

    // Jitter ±25 %: der erste Versuch liegt um 100 ms, der dritte um 400 ms.
    expect($policy->delayMicrosecondsFor(1))->toBeGreaterThanOrEqual(75 * 1000)
        ->and($policy->delayMicrosecondsFor(1))->toBeLessThanOrEqual(125 * 1000)
        ->and($policy->delayMicrosecondsFor(3))->toBeGreaterThanOrEqual(300 * 1000)
        ->and($policy->delayMicrosecondsFor(10))->toBeLessThanOrEqual(1250 * 1000, 'Deckel greift');
});

it('öffnet den Circuit erst ab der Schwelle und schließt ihn nach dem Cooldown', function (): void {
    $clock = new FrozenClock();
    $breaker = new CircuitBreaker(new ArrayCache($clock), $clock, threshold: 3, cooldownSeconds: 300);

    expect($breaker->isOpen('t'))->toBeFalse()
        ->and($breaker->secondsUntilRetry('t'))->toBe(0);

    $breaker->recordFailure('t');
    $breaker->recordFailure('t');
    expect($breaker->isOpen('t'))->toBeFalse('unterhalb der Schwelle bleibt er zu');

    $breaker->recordFailure('t');
    expect($breaker->isOpen('t'))->toBeTrue()
        ->and($breaker->secondsUntilRetry('t'))->toBe(300);

    $clock->advance(301);
    expect($breaker->isOpen('t'))->toBeFalse()
        ->and($breaker->secondsUntilRetry('t'))->toBe(0);
});

it('setzt den Circuit nach einem Erfolg zurück', function (): void {
    $clock = new FrozenClock();
    $breaker = new CircuitBreaker(new ArrayCache($clock), $clock, threshold: 2, cooldownSeconds: 300);

    $breaker->recordFailure('t');
    $breaker->recordFailure('t');
    expect($breaker->isOpen('t'))->toBeTrue();

    $breaker->recordSuccess('t');
    expect($breaker->isOpen('t'))->toBeFalse();
});

it('sperrt auf Zuruf für eine feste Zeit', function (): void {
    $clock = new FrozenClock();
    $breaker = new CircuitBreaker(new ArrayCache($clock), $clock, threshold: 5, cooldownSeconds: 300);

    $breaker->tripFor('t', 900);
    expect($breaker->isOpen('t'))->toBeTrue();

    $clock->advance(899);
    expect($breaker->isOpen('t'))->toBeTrue();

    $clock->advance(2);
    expect($breaker->isOpen('t'))->toBeFalse();
});

// ------------------------------------------------------------------- Gate

it('erklärt jeden State in einem verwertbaren Satz', function (): void {
    $gate = new EntitlementGate(new LicenseConfig());

    foreach (LicenseState::cases() as $state) {
        $status = new LicenseStatus(
            tenant: 't',
            key: 'WB-UMAN-1a2b3c4dQF',
            state: $state,
            source: StatusSource::Live,
            checkedAt: new DateTimeImmutable('@1789000000'),
        );

        expect(strlen($gate->explain($status)))->toBeGreaterThan(10);
    }
});

it('beantwortet allows() passend zur Entscheidung', function (): void {
    $status = new LicenseStatus(
        tenant: 't', key: 'k', state: LicenseState::Revoked,
        source: StatusSource::Live, checkedAt: new DateTimeImmutable('@1789000000'),
    );

    expect((new EntitlementGate(new LicenseConfig(enforcement: EnforcementMode::Enforce)))->allows($status))->toBeFalse()
        ->and((new EntitlementGate(new LicenseConfig(enforcement: EnforcementMode::Warn)))->allows($status))->toBeTrue();
});
