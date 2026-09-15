<?php

declare(strict_types=1);

namespace WissenBeratung\LicenseClient\Laravel;

use Illuminate\Console\Command;
use WissenBeratung\LicenseClient\Config\LicenseConfig;
use WissenBeratung\LicenseClient\EntitlementGate;
use WissenBeratung\LicenseClient\LicenseClient;

/**
 * Heartbeat für alle konfigurierten Mandanten.
 *
 * Gehört in den Scheduler, nie in den Request-Pfad. Der Command endet immer
 * mit Exit-Code 0, solange er selbst durchgelaufen ist: ein nicht
 * erreichbarer Lizenzserver ist ein Event, kein Fehlschlag des Dienstes —
 * sonst schlägt der Scheduler Alarm, obwohl alles wie vorgesehen arbeitet.
 * Mit --strict lässt sich das umdrehen.
 */
final class PingCommand extends Command
{
    protected $signature = 'wb:license:ping
                            {tenant?* : Mandanten; ohne Angabe alle konfigurierten}
                            {--force : Auch Mandanten prüfen, deren Intervall noch nicht abgelaufen ist}
                            {--strict : Exit-Code 1, wenn ein Mandant den Server nicht erreicht hat}';

    protected $description = 'Sendet den Lizenz-Heartbeat an den WB-Lizenzserver';

    public function handle(LicenseClient $client, EntitlementGate $gate, LicenseConfig $config): int
    {
        /** @var list<string> $argument */
        $argument = (array) $this->argument('tenant');
        $tenants = $argument !== []
            ? $argument
            : array_keys((array) config('wb-license.tenants', []));

        if ($tenants === []) {
            $this->warn('Keine Mandanten konfiguriert — nichts zu tun.');

            return self::SUCCESS;
        }

        $unreachable = 0;

        foreach ($tenants as $tenant) {
            if (! $this->option('force') && ! $client->isPingDue($tenant)) {
                $this->line(sprintf('  %-28s übersprungen (Intervall noch nicht abgelaufen)', $tenant));

                continue;
            }

            $status = $client->refresh($tenant);
            $decision = $gate->decide($status);

            $reached = $status->source->value === 'live';
            $unreachable += $reached ? 0 : 1;

            $this->line(sprintf(
                '  %-28s %-10s %-12s %-20s %s',
                $tenant,
                $status->state->value,
                $status->source->value,
                $decision->value,
                $status->maskedKey(),
            ));

            if (! $reached) {
                $this->warn('    ' . $gate->explain($status));
            }
        }

        if ($unreachable > 0) {
            $this->warn(sprintf(
                '%d von %d Mandanten ohne Serverkontakt. Local Grace trägt %d h.',
                $unreachable,
                count($tenants),
                intdiv($config->localGrace, 3600),
            ));

            return $this->option('strict') ? self::FAILURE : self::SUCCESS;
        }

        return self::SUCCESS;
    }
}
