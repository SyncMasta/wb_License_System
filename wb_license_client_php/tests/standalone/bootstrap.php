<?php

declare(strict_types=1);

/**
 * Bootstrap für den Offline-Selbsttest.
 *
 * Lädt das Paket ohne Composer und definiert die benötigten PSR-Interfaces,
 * falls kein vendor/ vorhanden ist. Das ist bewusst ein Notbehelf für
 * Umgebungen ohne Composer — in CI läuft Pest gegen die echten Pakete.
 *
 * Die hier definierten Interfaces sind bewusst minimal und entsprechen den
 * Signaturen, die dieses Paket tatsächlich verwendet.
 */

$vendor = __DIR__ . '/../../vendor/autoload.php';
if (is_file($vendor)) {
    require $vendor;
} else {
    if (! interface_exists(\Psr\Log\LoggerInterface::class)) {
        eval('
            namespace Psr\Log;
            interface LoggerInterface {
                public function emergency($message, array $context = []): void;
                public function alert($message, array $context = []): void;
                public function critical($message, array $context = []): void;
                public function error($message, array $context = []): void;
                public function warning($message, array $context = []): void;
                public function notice($message, array $context = []): void;
                public function info($message, array $context = []): void;
                public function debug($message, array $context = []): void;
                public function log($level, $message, array $context = []): void;
            }
            class NullLogger implements LoggerInterface {
                public function emergency($message, array $context = []): void {}
                public function alert($message, array $context = []): void {}
                public function critical($message, array $context = []): void {}
                public function error($message, array $context = []): void {}
                public function warning($message, array $context = []): void {}
                public function notice($message, array $context = []): void {}
                public function info($message, array $context = []): void {}
                public function debug($message, array $context = []): void {}
                public function log($level, $message, array $context = []): void {}
            }
        ');
    }

    if (! interface_exists(\Psr\SimpleCache\CacheInterface::class)) {
        eval('
            namespace Psr\SimpleCache;
            interface InvalidArgumentException extends \Throwable {}
            interface CacheInterface {
                public function get(string $key, mixed $default = null): mixed;
                public function set(string $key, mixed $value, null|int|\DateInterval $ttl = null): bool;
                public function delete(string $key): bool;
                public function clear(): bool;
                public function getMultiple(iterable $keys, mixed $default = null): iterable;
                public function setMultiple(iterable $values, null|int|\DateInterval $ttl = null): bool;
                public function deleteMultiple(iterable $keys): bool;
                public function has(string $key): bool;
            }
        ');
    }
}

// PSR-4-Autoloader für das Paket selbst.
spl_autoload_register(static function (string $class): void {
    $prefix = 'WissenBeratung\\LicenseClient\\';
    if (! str_starts_with($class, $prefix)) {
        return;
    }

    $relative = substr($class, strlen($prefix));
    $path = __DIR__ . '/../../src/' . str_replace('\\', '/', $relative) . '.php';
    if (is_file($path)) {
        require $path;
    }
});

require __DIR__ . '/fakes.php';
require __DIR__ . '/assert.php';
