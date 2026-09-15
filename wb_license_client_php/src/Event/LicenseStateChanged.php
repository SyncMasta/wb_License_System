<?php

declare(strict_types=1);

namespace WissenBeratung\LicenseClient\Event;

use WissenBeratung\LicenseClient\Enum\LicenseState;

/** Der Server meldet einen anderen State als beim letzten bekannten Stand. */
final readonly class LicenseStateChanged
{
    public function __construct(
        public string $tenant,
        public LicenseState $previous,
        public LicenseState $current,
    ) {}
}
