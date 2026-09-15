<?php

declare(strict_types=1);

namespace WissenBeratung\LicenseClient\Laravel;

use Illuminate\Contracts\Events\Dispatcher;
use WissenBeratung\LicenseClient\Contracts\EventDispatcher;

/**
 * Reicht die Lizenz-Events in den Laravel-Event-Bus weiter.
 *
 * Damit kann der Dienst sie wie jedes andere Event behandeln — Listener für
 * Zabbix, MonitorHub oder Telegram hängen dort an, nicht in diesem Paket.
 */
final readonly class LaravelEventDispatcher implements EventDispatcher
{
    public function __construct(private Dispatcher $dispatcher) {}

    public function dispatch(object $event): void
    {
        $this->dispatcher->dispatch($event);
    }
}
