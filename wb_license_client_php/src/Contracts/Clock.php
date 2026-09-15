<?php

declare(strict_types=1);

namespace WissenBeratung\LicenseClient\Contracts;

use DateTimeImmutable;

/**
 * Zeitquelle. Eigenes Interface statt psr/clock, damit der Kern eine
 * Abhängigkeit weniger hat — in Tests wird die Zeit gestellt, sonst käme
 * man an Cache-TTL und Local Grace nicht sauber heran.
 */
interface Clock
{
    public function now(): DateTimeImmutable;
}
