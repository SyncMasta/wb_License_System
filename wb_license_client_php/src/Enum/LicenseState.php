<?php

declare(strict_types=1);

namespace WissenBeratung\LicenseClient\Enum;

/**
 * Lizenz-Zustände. Die Werte entsprechen exakt denen im Odoo
 * (wb.license.key.state), plus `unknown` für den Fall, dass nie eine
 * verwertbare Serverantwort vorlag.
 */
enum LicenseState: string
{
    /** Schlüssel existiert, wurde aber nie aktiviert. */
    case Issued = 'issued';

    case Trial = 'trial';

    case Active = 'active';

    /** Rechnung überfällig — vom Mahnstatus des Partners abgeleitet. */
    case Grace = 'grace';

    case Expired = 'expired';

    case Revoked = 'revoked';

    case Cancelled = 'cancelled';

    /** Keine verwertbare Antwort. Kein Serverzustand, ein Clientzustand. */
    case Unknown = 'unknown';

    /**
     * Unbekannte Serverwerte werden zu `Unknown` statt zu einem Fehler.
     * Ein neuer State im Odoo darf einen laufenden Dienst nicht anhalten.
     */
    public static function fromServer(?string $value): self
    {
        return self::tryFrom((string) $value) ?? self::Unknown;
    }

    /** Läuft der Vertrag aus Sicht des Servers noch? */
    public function isEntitled(): bool
    {
        return match ($this) {
            self::Active, self::Trial, self::Grace, self::Issued => true,
            self::Expired, self::Revoked, self::Cancelled, self::Unknown => false,
        };
    }
}
