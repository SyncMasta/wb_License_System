<?php

declare(strict_types=1);

namespace WissenBeratung\LicenseClient\Tests\Support;

use WissenBeratung\LicenseClient\Contracts\LicenseTransport;
use WissenBeratung\LicenseClient\Http\TransportResponse;

/**
 * Transport mit vorgegebenen Antworten. Zählt die Aufrufe — damit lässt sich
 * prüfen, dass bei offenem Circuit gar nicht erst gefragt wird und dass ein
 * Retry stattgefunden hat.
 */
final class FakeTransport implements LicenseTransport
{
    /** @var list<TransportResponse> */
    private array $queue = [];

    public int $calls = 0;

    /** @var list<array{key: string, secret: string, params: array<string, mixed>}> */
    public array $received = [];

    private ?TransportResponse $always = null;

    public function queue(TransportResponse ...$responses): self
    {
        foreach ($responses as $response) {
            $this->queue[] = $response;
        }

        return $this;
    }

    public function always(TransportResponse $response): self
    {
        $this->always = $response;

        return $this;
    }

    public function call(string $endpoint, array $params, string $key = '', string $secret = ''): TransportResponse
    {
        $this->calls++;
        $this->received[] = ['key' => $key, 'secret' => $secret, 'params' => $params];

        if ($this->queue !== []) {
            return array_shift($this->queue);
        }

        return $this->always ?? TransportResponse::transportFailure('keine Antwort hinterlegt');
    }
}
