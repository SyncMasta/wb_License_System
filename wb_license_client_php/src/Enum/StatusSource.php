<?php

declare(strict_types=1);

namespace WissenBeratung\LicenseClient\Enum;

/**
 * Woher ein Status stammt. Entscheidend für die Beurteilung, wie belastbar
 * er ist — `LocalGrace` heißt: der Server war nicht erreichbar, wir arbeiten
 * mit einem veralteten Stand weiter.
 */
enum StatusSource: string
{
    /** Frische Serverantwort. */
    case Live = 'live';

    /** Aus dem Cache, innerhalb der TTL. */
    case Cache = 'cache';

    /** Server nicht erreichbar, letzter bekannter Stand gilt weiter. */
    case LocalGrace = 'local_grace';
}
