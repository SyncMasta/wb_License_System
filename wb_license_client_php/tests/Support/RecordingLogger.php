<?php

declare(strict_types=1);

namespace WissenBeratung\LicenseClient\Tests\Support;

/** Logger, der die Meldungen mitschreibt — für die Maskierungs-Prüfung. */
final class RecordingLogger implements \Psr\Log\LoggerInterface
{
    /** @var list<array{level: string, message: string, context: array<string, mixed>}> */
    public array $records = [];

    public function emergency($message, array $context = []): void { $this->log('emergency', $message, $context); }

    public function alert($message, array $context = []): void { $this->log('alert', $message, $context); }

    public function critical($message, array $context = []): void { $this->log('critical', $message, $context); }

    public function error($message, array $context = []): void { $this->log('error', $message, $context); }

    public function warning($message, array $context = []): void { $this->log('warning', $message, $context); }

    public function notice($message, array $context = []): void { $this->log('notice', $message, $context); }

    public function info($message, array $context = []): void { $this->log('info', $message, $context); }

    public function debug($message, array $context = []): void { $this->log('debug', $message, $context); }

    public function log($level, $message, array $context = []): void
    {
        $this->records[] = [
            'level' => (string) $level,
            'message' => (string) $message,
            'context' => $context,
        ];
    }

    /** Alles, was geloggt wurde, als ein durchsuchbarer String. */
    public function dump(): string
    {
        $out = '';
        foreach ($this->records as $record) {
            $out .= $record['level'] . ' ' . $record['message'] . ' ' . json_encode($record['context']) . "\n";
        }

        return $out;
    }
}
