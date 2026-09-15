<?php

declare(strict_types=1);

/**
 * Prüft die eingecheckten Interop-Vektoren gegen die aktuelle
 * Signature-Implementierung.
 *
 * Läuft ohne Composer und ohne Netzwerk — bewusst, damit der Check auch dort
 * funktioniert, wo nur ein PHP-Binary liegt.
 *
 * Aufruf: php tests/interop/verify_vectors.php
 * Exit-Code 0 = alles gleich, 1 = Abweichung.
 */

require __DIR__ . '/../../src/Http/Signature.php';

use WissenBeratung\LicenseClient\Http\Signature;

$path = __DIR__ . '/vectors.json';
if (! is_file($path)) {
    fwrite(STDERR, "vectors.json fehlt: {$path}\n");
    exit(1);
}

/** @var array{scheme: string, secret: string, cases: array<int, array<string, mixed>>} $vectors */
$vectors = json_decode((string) file_get_contents($path), true, 512, JSON_THROW_ON_ERROR);
$secret = (string) $vectors['secret'];
$failures = 0;

if ($vectors['scheme'] !== Signature::SCHEME) {
    fwrite(STDERR, "Schema-Mismatch: {$vectors['scheme']} != " . Signature::SCHEME . "\n");
    $failures++;
}

foreach ($vectors['cases'] as $case) {
    $name = (string) $case['name'];
    $key = (string) $case['key'];
    $timestamp = (int) $case['timestamp'];
    $nonce = (string) $case['nonce'];
    $body = (string) $case['body'];

    $checks = [
        'body_sha256' => hash('sha256', $body) === $case['body_sha256'],
        'signature_base' => Signature::base($key, $timestamp, $nonce, $body) === $case['signature_base'],
        'signature' => Signature::compute($secret, $key, $timestamp, $nonce, $body) === $case['signature'],
        'verify' => Signature::verify($secret, $key, $timestamp, $nonce, $body, 'v1=' . $case['signature']),
        'tampered_rejected' => ! Signature::verify($secret, $key, $timestamp, $nonce, $body . ' ', (string) $case['signature']),
        'wrong_secret_rejected' => ! Signature::verify('WBS-' . str_repeat('x', 43), $key, $timestamp, $nonce, $body, (string) $case['signature']),
    ];

    $failed = array_keys(array_filter($checks, static fn (bool $ok): bool => ! $ok));
    if ($failed !== []) {
        $failures++;
        fwrite(STDERR, "FAIL {$name}: " . implode(', ', $failed) . "\n");
    } else {
        echo "OK   {$name}\n";
    }
}

// Maskierung: ein Key darf nie vollständig in einem Log landen.
$masked = Signature::mask('WB-UMAN-1a2b3c4dQF');
if (str_contains($masked, '1a2b3c4d')) {
    $failures++;
    fwrite(STDERR, "FAIL mask: Key nicht ausreichend maskiert ({$masked})\n");
} else {
    echo "OK   mask ({$masked})\n";
}

if (Signature::headers('', 'WB-UMAN-1a2b3c4dQF', '{}') !== []) {
    $failures++;
    fwrite(STDERR, "FAIL headers: ohne Secret dürfen keine Header entstehen\n");
} else {
    echo "OK   headers ohne Secret leer\n";
}

$nonces = [];
for ($i = 0; $i < 500; $i++) {
    $nonces[Signature::nonce()] = true;
}
if (count($nonces) !== 500) {
    $failures++;
    fwrite(STDERR, "FAIL nonce: Kollision in 500 Ziehungen\n");
} else {
    echo "OK   nonce eindeutig\n";
}

echo $failures === 0 ? "\nAlle Interop-Vektoren stimmen.\n" : "\n{$failures} Abweichung(en).\n";
exit($failures === 0 ? 0 : 1);
