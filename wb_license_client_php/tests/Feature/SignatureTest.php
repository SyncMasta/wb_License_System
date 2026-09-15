<?php

declare(strict_types=1);

use WissenBeratung\LicenseClient\Http\Signature;

/**
 * Interop mit Server und Odoo-Client: dieselben Vektoren prüfen
 * wb_subscription/tests/test_hmac_signature.py und
 * wb_license_client/tests/test_signature.py.
 */

/** @return array{scheme: string, secret: string, cases: array<int, array<string, mixed>>} */
function vectors(): array
{
    $path = __DIR__ . '/../interop/vectors.json';

    return json_decode((string) file_get_contents($path), true, 512, JSON_THROW_ON_ERROR);
}

it('erzeugt denselben Signatur-String wie der Server', function (): void {
    foreach (vectors()['cases'] as $case) {
        expect(Signature::base($case['key'], $case['timestamp'], $case['nonce'], $case['body']))
            ->toBe($case['signature_base'], 'Fall: ' . $case['name']);
    }
});

it('erzeugt dieselbe Signatur wie der Server', function (): void {
    $secret = vectors()['secret'];

    foreach (vectors()['cases'] as $case) {
        expect(Signature::compute($secret, $case['key'], $case['timestamp'], $case['nonce'], $case['body']))
            ->toBe($case['signature'], 'Fall: ' . $case['name']);
    }
});

it('weist veränderte Bodys und falsche Secrets ab', function (): void {
    $v = vectors();
    $case = $v['cases'][0];

    expect(Signature::verify($v['secret'], $case['key'], $case['timestamp'], $case['nonce'], $case['body'], 'v1=' . $case['signature']))->toBeTrue()
        ->and(Signature::verify($v['secret'], $case['key'], $case['timestamp'], $case['nonce'], $case['body'] . ' ', $case['signature']))->toBeFalse()
        ->and(Signature::verify('WBS-' . str_repeat('x', 43), $case['key'], $case['timestamp'], $case['nonce'], $case['body'], $case['signature']))->toBeFalse();
});

it('liefert ohne Secret keine Header', function (): void {
    expect(Signature::headers('', 'WB-UMAN-1a2b3c4dQF', '{}'))->toBe([]);
});

it('setzt alle vier Header, wenn ein Secret vorliegt', function (): void {
    $headers = Signature::headers('WBS-' . str_repeat('a', 43), 'WB-UMAN-1a2b3c4dQF', '{}');

    expect(array_keys($headers))->toBe(['X-WB-Key', 'X-WB-Timestamp', 'X-WB-Nonce', 'X-WB-Signature'])
        ->and($headers['X-WB-Signature'])->toStartWith('v1=');
});

it('erzeugt eindeutige Nonces', function (): void {
    $nonces = [];
    for ($i = 0; $i < 500; $i++) {
        $nonces[Signature::nonce()] = true;
    }

    expect(count($nonces))->toBe(500);
});

it('maskiert Schlüssel so, dass der Mittelteil verschwindet', function (): void {
    expect(Signature::mask('WB-UMAN-1a2b3c4dQF'))->not->toContain('1a2b3c4d')
        ->and(Signature::mask('kurz'))->toBe('****');
});
