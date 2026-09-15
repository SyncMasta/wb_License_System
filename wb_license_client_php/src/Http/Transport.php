<?php

declare(strict_types=1);

namespace WissenBeratung\LicenseClient\Http;

use Psr\Http\Client\ClientExceptionInterface;
use Psr\Http\Client\ClientInterface;
use Psr\Http\Message\RequestFactoryInterface;
use Psr\Http\Message\StreamFactoryInterface;
use Psr\Log\LoggerInterface;
use Psr\Log\NullLogger;
use WissenBeratung\LicenseClient\Contracts\LicenseTransport;

/**
 * Transport für die Odoo-Endpunkte des WB-Lizenzservers.
 *
 * Zwei Eigenheiten des Servers bestimmen diese Klasse (siehe docs/CONTRACT.md):
 *
 * 1. Die Routen sind Odoo `type='json'`, also JSON-RPC 2.0 und nicht REST.
 *    Die Nutzdaten stehen im Request unter `params` und in der Antwort unter
 *    `result`.
 * 2. Der HTTP-Status ist auch im Fehlerfall 200. Fachliche Fehler kommen als
 *    String in `result.error`. Wer auf Statuscodes entscheidet, sieht
 *    dauerhaft "alles gut".
 *
 * Deshalb liefert diese Klasse eine Response, die beide Ebenen offenlegt,
 * und wirft keine Exceptions in den Aufrufpfad: Transportfehler werden zu
 * einer Response mit `transportError`.
 */
final class Transport implements LicenseTransport
{
    public function __construct(
        private readonly string $baseUrl,
        private readonly ClientInterface $httpClient,
        private readonly RequestFactoryInterface $requestFactory,
        private readonly StreamFactoryInterface $streamFactory,
        private readonly string $userAgent,
        private readonly LoggerInterface $logger = new NullLogger(),
    ) {}

    /**
     * Setzt einen signierten (oder unsignierten) Request ab.
     *
     * @param  array<string, mixed>  $params  Inhalt des JSON-RPC-`params`-Objekts
     * @param  string  $key     Public Key; leer, wenn der Aufruf keinen hat
     * @param  string  $secret  API-Secret; leer lassen, um unsigniert zu senden
     */
    public function call(string $endpoint, array $params, string $key = '', string $secret = ''): TransportResponse
    {
        // Genau diese Bytes werden signiert UND gesendet. Niemals neu
        // serialisieren — sonst weicht die Signatur vom Body ab.
        $body = $this->encodeEnvelope($params);

        $request = $this->requestFactory
            ->createRequest('POST', rtrim($this->baseUrl, '/') . $endpoint)
            ->withHeader('Content-Type', 'application/json')
            ->withHeader('Accept', 'application/json')
            ->withHeader('User-Agent', $this->userAgent)
            ->withBody($this->streamFactory->createStream($body));

        foreach (Signature::headers($secret, $key, $body) as $header => $value) {
            $request = $request->withHeader($header, $value);
        }

        try {
            $response = $this->httpClient->sendRequest($request);
        } catch (ClientExceptionInterface $e) {
            $this->logger->warning('[wb-license] Transportfehler bei {endpoint}: {message}', [
                'endpoint' => $endpoint,
                'message' => $e->getMessage(),
                'key' => Signature::mask($key),
            ]);

            return TransportResponse::transportFailure($e->getMessage());
        }

        $status = $response->getStatusCode();
        $decoded = json_decode((string) $response->getBody(), true);

        if (! is_array($decoded)) {
            $this->logger->warning('[wb-license] Unlesbare Antwort von {endpoint} (HTTP {status})', [
                'endpoint' => $endpoint,
                'status' => $status,
            ]);

            return TransportResponse::transportFailure("Unlesbare Antwort (HTTP {$status})", $status);
        }

        // JSON-RPC-Fehlerobjekt: Server-Exception, kommt ebenfalls mit HTTP 200.
        if (isset($decoded['error']) && is_array($decoded['error'])) {
            $message = is_string($decoded['error']['message'] ?? null)
                ? $decoded['error']['message']
                : 'Odoo Server Error';

            return TransportResponse::serverFault($message, $status);
        }

        $result = is_array($decoded['result'] ?? null) ? $decoded['result'] : [];

        // Fachlicher Fehler als String in result.error.
        if (isset($result['error']) && is_string($result['error'])) {
            return TransportResponse::apiError($result['error'], $result, $status);
        }

        return TransportResponse::success($result, $status);
    }

    /**
     * Baut den JSON-RPC-Envelope.
     *
     * `JSON_UNESCAPED_SLASHES` und `JSON_UNESCAPED_UNICODE` halten den Body
     * lesbar; für die Signatur ist die konkrete Wahl egal, solange derselbe
     * String signiert und gesendet wird.
     *
     * @param  array<string, mixed>  $params
     */
    private function encodeEnvelope(array $params): string
    {
        $encoded = json_encode([
            'jsonrpc' => '2.0',
            'method' => 'call',
            'params' => (object) $params,
        ], JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);

        // json_encode kann bei ungültigem UTF-8 false liefern. Ein leerer
        // Envelope ist immer noch besser als ein Fatal im Request-Pfad des
        // aufrufenden Dienstes; der Server antwortet dann mit einem Fehler.
        return $encoded !== false ? $encoded : '{"jsonrpc":"2.0","method":"call","params":{}}';
    }
}
