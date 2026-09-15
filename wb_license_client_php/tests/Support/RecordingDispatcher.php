<?php

declare(strict_types=1);

namespace WissenBeratung\LicenseClient\Tests\Support;

use WissenBeratung\LicenseClient\Contracts\EventDispatcher;

/** Sammelt Events, damit Tests sie zählen können. */
final class RecordingDispatcher implements EventDispatcher
{
    /** @var list<object> */
    public array $events = [];

    public function dispatch(object $event): void
    {
        $this->events[] = $event;
    }

    /** @return list<object> */
    public function ofType(string $class): array
    {
        return array_values(array_filter($this->events, static fn (object $e): bool => $e instanceof $class));
    }

    public function countOf(string $class): int
    {
        return count($this->ofType($class));
    }
}
