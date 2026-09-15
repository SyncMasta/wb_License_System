<?php

declare(strict_types=1);

namespace WissenBeratung\LicenseClient\Event;

/**
 * Server nicht erreichbar, es wird aus Local Grace bedient.
 *
 * `remainingGraceSeconds` sagt, wie lange das noch trägt — das ist die
 * Zahl, die ins Monitoring gehört, nicht die bloße Tatsache des Ausfalls.
 */
final readonly class LicenseServerUnreachable
{
    public function __construct(
        public string $tenant,
        public int $remainingGraceSeconds,
        public ?string $reason = null,
    ) {}
}
