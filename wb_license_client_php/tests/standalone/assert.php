<?php

declare(strict_types=1);

/**
 * Winziger Test-Runner für den Offline-Selbsttest. Kein Ersatz für Pest —
 * aber er läuft überall, wo ein PHP-Binary liegt, und deckt dieselben
 * Szenarien ab.
 */
final class TestRunner
{
    private int $passed = 0;

    /** @var list<string> */
    private array $failures = [];

    private string $current = '';

    public function test(string $name, callable $fn): void
    {
        $this->current = $name;
        try {
            $fn($this);
            $this->passed++;
            echo "OK   {$name}\n";
        } catch (Throwable $e) {
            $this->failures[] = $name.': '.$e->getMessage();
            echo "FAIL {$name}\n     ".$e->getMessage()."\n";
        }
    }

    public function assert(bool $condition, string $message): void
    {
        if (! $condition) {
            throw new RuntimeException($message);
        }
    }

    public function same(mixed $expected, mixed $actual, string $message = ''): void
    {
        if ($expected !== $actual) {
            throw new RuntimeException(sprintf(
                '%s erwartet %s, bekommen %s',
                $message !== '' ? $message.':' : '',
                var_export($expected instanceof BackedEnum ? $expected->value : $expected, true),
                var_export($actual instanceof BackedEnum ? $actual->value : $actual, true),
            ));
        }
    }

    public function summary(): int
    {
        echo "\n{$this->passed} bestanden, ".count($this->failures)." fehlgeschlagen\n";
        foreach ($this->failures as $failure) {
            echo "  - {$failure}\n";
        }

        return $this->failures === [] ? 0 : 1;
    }
}
