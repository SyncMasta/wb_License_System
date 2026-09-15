<?php

declare(strict_types=1);

namespace WissenBeratung\LicenseClient\Enum;

/**
 * Wie hart die Lizenzentscheidung wirkt.
 *
 * Der Default ist `Warn` und das ist Absicht: der erste produktive Dienst
 * hängt an einem Lizenzsystem, das noch nie gelaufen ist. Ein unerprobtes
 * System gehört nicht ohne Notausgang auf einen kundenkritischen Pfad.
 */
enum EnforcementMode: string
{
    /** Prüfung läuft, Ergebnis wird nur geloggt. Entscheidung immer Process. */
    case Off = 'off';

    /** Wie Off, zusätzlich Alarm bei Hold-Zuständen. */
    case Warn = 'warn';

    /** Die Entscheidung wirkt. */
    case Enforce = 'enforce';

    public static function fromString(?string $value): self
    {
        return self::tryFrom(strtolower(trim((string) $value))) ?? self::Warn;
    }
}
