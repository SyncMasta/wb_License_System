<?php

declare(strict_types=1);

namespace WissenBeratung\LicenseClient\Contracts;

/**
 * Nimmt die Lizenz-Events entgegen und reicht sie an das Monitoring
 * weiter (Zabbix, WB MonitorHub, Laravel-Events — Sache des Dienstes).
 *
 * Absichtlich minimal gehalten: ein PSR-14-Dispatcher lässt sich mit drei
 * Zeilen dahinterhängen, aber der Kern soll die Abhängigkeit nicht tragen.
 */
interface EventDispatcher
{
    public function dispatch(object $event): void;
}
