<?php

declare(strict_types=1);

namespace WissenBeratung\LicenseClient\Event;

use WissenBeratung\LicenseClient\Enum\Decision;
use WissenBeratung\LicenseClient\Enum\LicenseState;

/**
 * Die Entscheidung wäre `Hold`.
 *
 * Wird IMMER ausgelöst, wenn der Zustand das hergibt — auch in den Modi
 * `off` und `warn`, wo die effektive Entscheidung Process bleibt. Sonst
 * würde ein gesperrter Mandant im Warn-Modus unbemerkt weiterlaufen, und
 * genau dafür ist der Modus nicht da.
 */
final readonly class LicenseBlocked
{
    public function __construct(
        public string $tenant,
        public LicenseState $state,
        public Decision $effectiveDecision,
    ) {}
}
