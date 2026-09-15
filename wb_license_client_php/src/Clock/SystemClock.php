<?php

declare(strict_types=1);

namespace WissenBeratung\LicenseClient\Clock;

use DateTimeImmutable;
use DateTimeZone;
use WissenBeratung\LicenseClient\Contracts\Clock;

/** Systemzeit in UTC. */
final class SystemClock implements Clock
{
    public function now(): DateTimeImmutable
    {
        return new DateTimeImmutable('now', new DateTimeZone('UTC'));
    }
}
