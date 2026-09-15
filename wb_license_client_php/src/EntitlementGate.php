<?php

declare(strict_types=1);

namespace WissenBeratung\LicenseClient;

use Psr\Log\LoggerInterface;
use Psr\Log\NullLogger;
use WissenBeratung\LicenseClient\Config\LicenseConfig;
use WissenBeratung\LicenseClient\Contracts\EventDispatcher;
use WissenBeratung\LicenseClient\Dto\LicenseStatus;
use WissenBeratung\LicenseClient\Enum\Decision;
use WissenBeratung\LicenseClient\Enum\EnforcementMode;
use WissenBeratung\LicenseClient\Enum\LicenseState;
use WissenBeratung\LicenseClient\Event\LicenseBlocked;
use WissenBeratung\LicenseClient\Event\NullEventDispatcher;

/**
 * Übersetzt einen Lizenzstatus in eine Handlungsanweisung.
 *
 * Der aufrufende Dienst soll nicht selbst aus dem State schließen müssen —
 * sonst steht die Fachlogik des Lizenzsystems in jedem Dienst noch einmal,
 * leicht anders.
 *
 * **`Hold` heißt anhalten und alarmieren, niemals verwerfen.** Im
 * Bewerberkontext: Vorgang annehmen, verschlüsselt parken, beide Seiten
 * alarmieren, nach Reaktivierung nacharbeiten. Es darf unter keinen
 * Umständen dazu führen, dass eine Bewerbung verloren geht. Das Parken setzt
 * der Dienst um — dieser Gate liefert nur die Entscheidung.
 */
final readonly class EntitlementGate
{
    public function __construct(
        private LicenseConfig $config,
        private EventDispatcher $events = new NullEventDispatcher,
        private LoggerInterface $logger = new NullLogger,
    ) {}

    public function decide(LicenseStatus $status): Decision
    {
        $base = $this->baseDecision($status);
        $effective = $this->applyEnforcement($base);

        if ($base === Decision::Hold) {
            // Auch in `off` und `warn` — sonst liefe ein gesperrter Mandant
            // im Warn-Modus unbemerkt weiter, und genau dafür ist der Modus
            // nicht gedacht.
            $this->events->dispatch(new LicenseBlocked($status->tenant, $status->state, $effective));
        }

        if ($effective !== $base) {
            $this->logger->warning(
                '[wb-license] {tenant}: Entscheidung {base} wird im Modus {mode} zu {effective} '
                .'(State {state}, Quelle {source})',
                [
                    'tenant' => $status->tenant,
                    'base' => $base->value,
                    'effective' => $effective->value,
                    'mode' => $this->config->enforcement->value,
                    'state' => $status->state->value,
                    'source' => $status->source->value,
                    'key' => $status->maskedKey(),
                ],
            );
        }

        return $effective;
    }

    /** Darf verarbeitet werden? Kurzform für den häufigsten Aufruf. */
    public function allows(LicenseStatus $status): bool
    {
        return $this->decide($status)->allowsProcessing();
    }

    /**
     * Ein Satz Klartext für Log, Banner oder Alarmtext.
     */
    public function explain(LicenseStatus $status): string
    {
        return match ($status->state) {
            LicenseState::Active => 'Lizenz aktiv.',
            LicenseState::Trial => 'Testlizenz aktiv.',
            LicenseState::Grace => 'Rechnung überfällig — Lizenz läuft in der Nachfrist weiter.',
            LicenseState::Issued => 'Lizenzschlüssel existiert, wurde aber nie aktiviert. '
                .'Für einen serverseitig angelegten Schlüssel ist das ein Einrichtungsfehler.',
            LicenseState::Expired => 'Lizenz abgelaufen.',
            LicenseState::Revoked => 'Lizenz gesperrt.',
            LicenseState::Cancelled => 'Lizenz gekündigt.',
            LicenseState::Unknown => 'Kein Lizenzstatus verfügbar — Lizenzserver nicht erreichbar '
                .'und Local Grace erschöpft.',
        };
    }

    /**
     * Die fachliche Entscheidung, noch ohne Enforcement-Modus.
     */
    private function baseDecision(LicenseStatus $status): Decision
    {
        return match ($status->state) {
            LicenseState::Active, LicenseState::Trial => Decision::Process,
            LicenseState::Grace => Decision::ProcessWithWarning,
            // Schlüssel existiert, ist aber nie aktiviert worden. Bei
            // PHP-Diensten legt WB den Schlüssel direkt als `active` oder NFR
            // an — `issued` deutet hier auf einen Einrichtungsfehler hin,
            // rechtfertigt aber keinen Stillstand.
            LicenseState::Issued => Decision::ProcessWithWarning,
            LicenseState::Expired, LicenseState::Revoked, LicenseState::Cancelled => Decision::Hold,
            LicenseState::Unknown => Decision::Hold,
        };
    }

    private function applyEnforcement(Decision $decision): Decision
    {
        return match ($this->config->enforcement) {
            EnforcementMode::Off, EnforcementMode::Warn => $decision === Decision::Hold
                ? Decision::Process
                : $decision,
            EnforcementMode::Enforce => $decision,
        };
    }
}
