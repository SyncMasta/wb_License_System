<?php

declare(strict_types=1);

use Nyholm\Psr7\Factory\Psr17Factory;
use Nyholm\Psr7\Response;
use Psr\Http\Client\ClientExceptionInterface;
use Psr\Http\Client\ClientInterface;
use Psr\Http\Message\RequestInterface;
use Psr\Http\Message\ResponseInterface;
use WissenBeratung\LicenseClient\Http\Signature;
use WissenBeratung\LicenseClient\Http\Transport;

/**
 * Transport gegen einen gemockten PSR-18-Client. Keine echten Aufrufe.
 *
 * Der Schwerpunkt liegt auf den beiden Eigenheiten des Odoo-Servers: der
 * JSON-RPC-Envelope und der Umstand, dass auch Fehler mit HTTP 200 kommen.
 */

/** Sammelt den abgesetzten Request und liefert eine vorgegebene Antwort. */
final class FakeHttpClient implements ClientInterface
{
    public ?RequestInterface $lastRequest = null;

    public function __construct(
        private readonly ?ResponseInterface $response = null,
        private readonly ?ClientExceptionInterface $exception = null,
    ) {}

    public function sendRequest(RequestInterface $request): ResponseInterface
    {
        $this->lastRequest = $request;

        if ($this->exception !== null) {
            throw $this->exception;
        }

        return $this->response ?? new Response(200, [], '{}');
    }
}

final class FakeClientException extends RuntimeException implements ClientExceptionInterface {}

function transportWith(?ResponseInterface $response, ?ClientExceptionInterface $exception = null): array
{
    $factory = new Psr17Factory;
    $client = new FakeHttpClient($response, $exception);

    return [
        new Transport(
            baseUrl: 'https://my.wissen-beratung.de/',
            httpClient: $client,
            requestFactory: $factory,
            streamFactory: $factory,
            userAgent: 'wb-license-client-php/0.1.0 (wissen-api)',
        ),
        $client,
    ];
}

it('verpackt die Parameter in einen JSON-RPC-Envelope', function (): void {
    [$transport, $client] = transportWith(new Response(200, [], '{"result":{"state":"active"}}'));

    $transport->call('/api/license/check', ['key' => 'WB-UMAN-1a2b3c4dQF']);

    $body = (string) $client->lastRequest->getBody();
    $decoded = json_decode($body, true);

    expect($decoded['jsonrpc'])->toBe('2.0')
        ->and($decoded['method'])->toBe('call')
        ->and($decoded['params']['key'])->toBe('WB-UMAN-1a2b3c4dQF')
        ->and((string) $client->lastRequest->getUri())->toBe('https://my.wissen-beratung.de/api/license/check')
        ->and($client->lastRequest->getHeaderLine('Content-Type'))->toBe('application/json')
        ->and($client->lastRequest->getHeaderLine('User-Agent'))->toBe('wb-license-client-php/0.1.0 (wissen-api)');
});

it('signiert genau die Bytes, die es auch sendet', function (): void {
    [$transport, $client] = transportWith(new Response(200, [], '{"result":{}}'));
    $secret = 'WBS-'.str_repeat('a', 43);
    $key = 'WB-UMAN-1a2b3c4dQF';

    $transport->call('/api/license/check', ['key' => $key], $key, $secret);

    $request = $client->lastRequest;
    $sentBody = (string) $request->getBody();

    // Genau diese Prüfung fängt den Klassiker: Body neu serialisiert,
    // Signatur passt nicht mehr.
    expect(Signature::verify(
        $secret,
        $key,
        (int) $request->getHeaderLine('X-WB-Timestamp'),
        $request->getHeaderLine('X-WB-Nonce'),
        $sentBody,
        $request->getHeaderLine('X-WB-Signature'),
    ))->toBeTrue();
});

it('sendet ohne Secret keine Signatur-Header', function (): void {
    [$transport, $client] = transportWith(new Response(200, [], '{"result":{}}'));

    $transport->call('/api/license/check', ['key' => 'WB-UMAN-1a2b3c4dQF'], 'WB-UMAN-1a2b3c4dQF');

    expect($client->lastRequest->hasHeader('X-WB-Signature'))->toBeFalse();
});

it('packt den result-Inhalt aus', function (): void {
    [$transport] = transportWith(new Response(200, [], '{"jsonrpc":"2.0","id":null,"result":{"state":"active","valid_to":"2026-12-31"}}'));

    $response = $transport->call('/api/license/check', []);

    expect($response->ok)->toBeTrue()
        ->and($response->data['state'])->toBe('active')
        ->and($response->errorCode)->toBeNull()
        ->and($response->retryable)->toBeFalse();
});

it('erkennt fachliche Fehler trotz HTTP 200', function (): void {
    [$transport] = transportWith(new Response(200, [], '{"result":{"error":"KEY_NOT_FOUND"}}'));

    $response = $transport->call('/api/license/check', []);

    expect($response->ok)->toBeFalse()
        ->and($response->errorCode)->toBe('KEY_NOT_FOUND')
        ->and($response->retryable)->toBeFalse('fachliche Fehler wiederholt man nicht');
});

it('erkennt das Rate-Limit und die Signaturfehler als solche', function (string $code, bool $rateLimited, bool $signature): void {
    [$transport] = transportWith(new Response(200, [], json_encode(['result' => ['error' => $code]])));

    $response = $transport->call('/api/license/check', []);

    expect($response->isRateLimited())->toBe($rateLimited)
        ->and($response->isSignatureProblem())->toBe($signature);
})->with([
    ['TOO_MANY_REQUESTS', true, false],
    ['TOO_MANY_ATTEMPTS', true, false],
    ['SIGNATURE_INVALID', false, true],
    ['SIGNATURE_REQUIRED', false, true],
    ['SIGNATURE_TIMESTAMP', false, true],
    ['SIGNATURE_REPLAY', false, true],
    ['KEY_NOT_FOUND', false, false],
]);

it('behandelt ein JSON-RPC-Fehlerobjekt als wiederholbaren Serverfehler', function (): void {
    [$transport] = transportWith(new Response(200, [], '{"jsonrpc":"2.0","error":{"code":200,"message":"Odoo Server Error"}}'));

    $response = $transport->call('/api/license/check', []);

    expect($response->ok)->toBeFalse()
        ->and($response->errorCode)->toBe('SERVER_FAULT')
        ->and($response->message)->toBe('Odoo Server Error')
        ->and($response->retryable)->toBeTrue();
});

it('behandelt unlesbare Antworten als Transportfehler', function (string $payload, int $status): void {
    [$transport] = transportWith(new Response($status, [], $payload));

    $response = $transport->call('/api/license/check', []);

    expect($response->ok)->toBeFalse()
        ->and($response->errorCode)->toBe('TRANSPORT_FAILURE')
        ->and($response->retryable)->toBeTrue()
        ->and($response->httpStatus)->toBe($status);
})->with([
    ['<html>502 Bad Gateway</html>', 502],
    ['', 200],
]);

it('macht aus einer Client-Exception eine Antwort statt eines Wurfs', function (): void {
    [$transport] = transportWith(null, new FakeClientException('cURL error 28: timeout'));

    $response = $transport->call('/api/license/check', []);

    expect($response->ok)->toBeFalse()
        ->and($response->errorCode)->toBe('TRANSPORT_FAILURE')
        ->and($response->message)->toContain('timeout')
        ->and($response->retryable)->toBeTrue()
        ->and($response->httpStatus)->toBe(0);
});

it('liefert auch ohne result-Feld eine verwertbare Antwort', function (): void {
    [$transport] = transportWith(new Response(200, [], '{"jsonrpc":"2.0","id":null}'));

    $response = $transport->call('/api/license/check', []);

    expect($response->ok)->toBeTrue()
        ->and($response->data)->toBe([]);
});
