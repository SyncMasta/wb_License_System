<?php

declare(strict_types=1);

namespace WissenBeratung\LicenseClient\Laravel;

use Http\Discovery\Psr17FactoryDiscovery;
use Http\Discovery\Psr18ClientDiscovery;
use Illuminate\Contracts\Cache\Repository as CacheRepository;
use Illuminate\Contracts\Events\Dispatcher;
use Illuminate\Support\ServiceProvider;
use Psr\Http\Client\ClientInterface;
use Psr\Http\Message\RequestFactoryInterface;
use Psr\Http\Message\StreamFactoryInterface;
use Psr\Log\LoggerInterface;
use Psr\SimpleCache\CacheInterface;
use WissenBeratung\LicenseClient\Cache\PsrStatusCache;
use WissenBeratung\LicenseClient\Clock\SystemClock;
use WissenBeratung\LicenseClient\Config\LicenseConfig;
use WissenBeratung\LicenseClient\Contracts\Clock;
use WissenBeratung\LicenseClient\Contracts\EventDispatcher;
use WissenBeratung\LicenseClient\Contracts\LicenseTransport;
use WissenBeratung\LicenseClient\Contracts\StatusCache;
use WissenBeratung\LicenseClient\Contracts\TenantKeyResolver;
use WissenBeratung\LicenseClient\EntitlementGate;
use WissenBeratung\LicenseClient\Http\Transport;
use WissenBeratung\LicenseClient\LicenseClient;
use WissenBeratung\LicenseClient\Resilience\CircuitBreaker;
use WissenBeratung\LicenseClient\Tenant\ArrayTenantKeyResolver;

/**
 * Laravel-Anbindung.
 *
 * Der Kern importiert nichts aus illuminate/* — nur diese Datei und der
 * Ping-Command tun das. Wer das Paket ohne Laravel nutzt, lädt sie nie.
 *
 * Alle Bindings sind über `$app->bind()` überschreibbar: wer einen eigenen
 * TenantKeyResolver (etwa aus der Datenbank) oder einen eigenen
 * EventDispatcher einhängen will, registriert ihn einfach vorher.
 */
final class LicenseServiceProvider extends ServiceProvider
{
    public function register(): void
    {
        $this->mergeConfigFrom(__DIR__.'/../../config/wb-license.php', 'wb-license');

        $this->app->singleton(LicenseConfig::class, function (): LicenseConfig {
            /** @var array<string, mixed> $config */
            $config = $this->app['config']->get('wb-license', []);

            return LicenseConfig::fromArray($config);
        });

        $this->app->bind(Clock::class, SystemClock::class);

        $this->app->bind(TenantKeyResolver::class, function (): TenantKeyResolver {
            /** @var array<string, array{key?: string, secret?: string, instance_id?: string}> $tenants */
            $tenants = $this->app['config']->get('wb-license.tenants', []);

            return new ArrayTenantKeyResolver($tenants);
        });

        $this->app->bind(StatusCache::class, fn (): StatusCache => new PsrStatusCache($this->psrCache()));

        $this->app->bind(LicenseTransport::class, function (): LicenseTransport {
            $config = $this->app->make(LicenseConfig::class);

            return new Transport(
                baseUrl: $config->baseUrl,
                httpClient: $this->app->bound(ClientInterface::class)
                    ? $this->app->make(ClientInterface::class)
                    : Psr18ClientDiscovery::find(),
                requestFactory: $this->app->bound(RequestFactoryInterface::class)
                    ? $this->app->make(RequestFactoryInterface::class)
                    : Psr17FactoryDiscovery::findRequestFactory(),
                streamFactory: $this->app->bound(StreamFactoryInterface::class)
                    ? $this->app->make(StreamFactoryInterface::class)
                    : Psr17FactoryDiscovery::findStreamFactory(),
                userAgent: $config->userAgent(),
                logger: $this->app->make(LoggerInterface::class),
            );
        });

        $this->app->singleton(CircuitBreaker::class, function (): CircuitBreaker {
            $config = $this->app->make(LicenseConfig::class);

            return new CircuitBreaker(
                cache: $this->psrCache(),
                clock: $this->app->make(Clock::class),
                threshold: $config->circuitThreshold,
                cooldownSeconds: $config->circuitCooldown,
            );
        });

        $this->app->singleton(LicenseClient::class, function (): LicenseClient {
            return new LicenseClient(
                config: $this->app->make(LicenseConfig::class),
                transport: $this->app->make(LicenseTransport::class),
                resolver: $this->app->make(TenantKeyResolver::class),
                cache: $this->app->make(StatusCache::class),
                clock: $this->app->make(Clock::class),
                circuitBreaker: $this->app->make(CircuitBreaker::class),
                events: $this->app->make(EventDispatcher::class),
                logger: $this->app->make(LoggerInterface::class),
            );
        });

        $this->app->singleton(EntitlementGate::class, function (): EntitlementGate {
            return new EntitlementGate(
                config: $this->app->make(LicenseConfig::class),
                events: $this->app->make(EventDispatcher::class),
                logger: $this->app->make(LoggerInterface::class),
            );
        });

        // Default: Events gehen in den Laravel-Event-Bus. Der Dienst hängt
        // dort seine Listener für Zabbix bzw. MonitorHub an.
        $this->app->bindIf(EventDispatcher::class, fn (): EventDispatcher => new LaravelEventDispatcher(
            $this->app->make(Dispatcher::class)
        ));
    }

    public function boot(): void
    {
        if ($this->app->runningInConsole()) {
            $this->publishes([
                __DIR__.'/../../config/wb-license.php' => $this->app->configPath('wb-license.php'),
            ], 'wb-license-config');

            $this->commands([PingCommand::class]);
        }
    }

    /**
     * PSR-16-Sicht auf den Laravel-Cache.
     *
     * Wichtig: ein Store, der Neustarts überlebt (Redis, Memcached, File).
     * Mit `array` als Store trägt Local Grace nichts — dann steht der Dienst
     * bei einem Serverausfall sofort auf `unknown`.
     */
    private function psrCache(): CacheInterface
    {
        $store = $this->app['config']->get('wb-license.cache_store');

        /** @var CacheRepository $repository */
        $repository = $store !== null
            ? $this->app->make('cache')->store($store)
            : $this->app->make('cache')->store();

        return $repository;
    }
}
