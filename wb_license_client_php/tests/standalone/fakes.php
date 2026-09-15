<?php

declare(strict_types=1);

/**
 * Lädt die Test-Doubles für den Offline-Selbsttest.
 *
 * Dieselben Klassen nutzt die Pest-Suite über autoload-dev — sie liegen
 * deshalb in tests/Support/ und nicht hier, damit es sie nur einmal gibt.
 */
foreach (glob(__DIR__.'/../Support/*.php') ?: [] as $file) {
    require_once $file;
}
