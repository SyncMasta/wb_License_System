<?php

declare(strict_types=1);

namespace WissenBeratung\LicenseClient\Tests\Support;

use DateTimeImmutable;
use DateTimeZone;
use WissenBeratung\LicenseClient\Contracts\Clock;

final class FrozenClock implements Clock
{
    public function __construct(private int $timestamp = 1789000000) {}

    public function now(): DateTimeImmutable
    {
        return new DateTimeImmutable('@'.$this->timestamp, new DateTimeZone('UTC'));
    }

    public function advance(int $seconds): void
    {
        $this->timestamp += $seconds;
    }
}
