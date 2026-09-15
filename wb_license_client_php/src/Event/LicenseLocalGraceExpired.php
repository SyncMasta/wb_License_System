<?php

declare(strict_types=1);

namespace WissenBeratung\LicenseClient\Event;

/**
 * Der letzte bekannte Status ist älter als die Local Grace. Ab hier gilt
 * `unknown`, und im Modus `enforce` bedeutet das Hold.
 *
 * Der kritischste Alarm des Clients: entweder ist unser Odoo seit Tagen
 * unerreichbar, oder der Dienst kommt aus einem anderen Grund nicht mehr
 * dorthin. Beides will man wissen, bevor die Verarbeitung steht.
 */
final readonly class LicenseLocalGraceExpired
{
    public function __construct(
        public string $tenant,
        public int $lastSeenSecondsAgo,
    ) {}
}
