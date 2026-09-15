<?php

declare(strict_types=1);

namespace WissenBeratung\LicenseClient\Enum;

/**
 * Was der aufrufende Dienst tun soll.
 *
 * `Hold` heißt anhalten und alarmieren, NIEMALS verwerfen. Im Bewerberkontext
 * bedeutet das: Vorgang annehmen, verschlüsselt parken, beide Seiten
 * alarmieren, nach Reaktivierung nacharbeiten. Die Umsetzung des Parkens
 * liegt beim aufrufenden Dienst — dieser Client liefert nur die Entscheidung.
 */
enum Decision: string
{
    case Process = 'process';

    case ProcessWithWarning = 'process_with_warning';

    case Hold = 'hold';

    public function allowsProcessing(): bool
    {
        return $this !== self::Hold;
    }
}
