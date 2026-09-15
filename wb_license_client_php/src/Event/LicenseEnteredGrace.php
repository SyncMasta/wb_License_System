<?php

declare(strict_types=1);

namespace WissenBeratung\LicenseClient\Event;

use DateTimeImmutable;

/**
 * Die Lizenz ist in die Grace-Period gewechselt — beim WB-Server heißt das:
 * eine Rechnung ist überfällig. Kaufmännisches Signal, kein technisches.
 */
final readonly class LicenseEnteredGrace
{
    public function __construct(
        public string $tenant,
        public ?DateTimeImmutable $graceUntil = null,
    ) {}
}
