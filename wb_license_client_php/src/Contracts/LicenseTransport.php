<?php

declare(strict_types=1);

namespace WissenBeratung\LicenseClient\Contracts;

use WissenBeratung\LicenseClient\Http\TransportResponse;

/**
 * Setzt einen Aufruf gegen den Lizenzserver ab.
 *
 * Eigenes Interface, damit der Client ohne echten HTTP-Stack testbar bleibt —
 * die Testszenarien (Timeout, 429, Circuit offen) wären sonst nur über einen
 * gemockten PSR-18-Client erreichbar, was jeden Test aufbläht.
 *
 * Implementierungen dürfen NICHT werfen: Transportfehler gehören als
 * TransportResponse zurück, nicht als Exception in den Aufrufpfad.
 */
interface LicenseTransport
{
    /**
     * @param  array<string, mixed>  $params  Inhalt des JSON-RPC-`params`-Objekts
     */
    public function call(string $endpoint, array $params, string $key = '', string $secret = ''): TransportResponse;
}
