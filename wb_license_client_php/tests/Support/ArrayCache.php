<?php

declare(strict_types=1);

namespace WissenBeratung\LicenseClient\Tests\Support;

use Psr\SimpleCache\CacheInterface;

/** PSR-16-Cache im Prozessspeicher, mit stellbarer Zeit für TTL-Tests. */
final class ArrayCache implements CacheInterface
{
    /** @var array<string, array{value: mixed, expires: int|null}> */
    private array $store = [];

    public function __construct(private readonly FrozenClock $clock) {}

    public function get(string $key, mixed $default = null): mixed
    {
        $entry = $this->store[$key] ?? null;
        if ($entry === null) {
            return $default;
        }

        if ($entry['expires'] !== null && $entry['expires'] <= $this->clock->now()->getTimestamp()) {
            unset($this->store[$key]);

            return $default;
        }

        return $entry['value'];
    }

    public function set(string $key, mixed $value, null|int|\DateInterval $ttl = null): bool
    {
        $this->store[$key] = [
            'value' => $value,
            'expires' => is_int($ttl) ? $this->clock->now()->getTimestamp() + $ttl : null,
        ];

        return true;
    }

    public function delete(string $key): bool
    {
        unset($this->store[$key]);

        return true;
    }

    public function clear(): bool
    {
        $this->store = [];

        return true;
    }

    public function getMultiple(iterable $keys, mixed $default = null): iterable
    {
        $out = [];
        foreach ($keys as $key) {
            $out[$key] = $this->get($key, $default);
        }

        return $out;
    }

    public function setMultiple(iterable $values, null|int|\DateInterval $ttl = null): bool
    {
        foreach ($values as $key => $value) {
            $this->set((string) $key, $value, $ttl);
        }

        return true;
    }

    public function deleteMultiple(iterable $keys): bool
    {
        foreach ($keys as $key) {
            $this->delete((string) $key);
        }

        return true;
    }

    public function has(string $key): bool
    {
        return $this->get($key) !== null;
    }
}
