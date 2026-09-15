<?php

declare(strict_types=1);

/**
 * Erzeugt die Interop-Testvektoren für das Signatur-Schema v1.
 *
 * Die erzeugte vectors.json wird von zwei Seiten geprüft:
 *
 * - PHP:    tests/interop/verify_vectors.php
 * - Python: wb_subscription/tests/test_hmac_signature.py (gegen die echten
 *           Model-Methoden von wb.key.generator)
 *
 * Damit ist festgenagelt, dass Client und Server denselben String signieren.
 * Neu erzeugen nur, wenn sich das Schema absichtlich ändert — die Datei ist
 * eine Regressionsbremse, kein Build-Artefakt.
 *
 * Aufruf: php tests/interop/generate_vectors.php > tests/interop/vectors.json
 */

require __DIR__.'/../../src/Http/Signature.php';

use WissenBeratung\LicenseClient\Http\Signature;

$secret = 'WBS-TESTSECRETTESTSECRETTESTSECRETTESTSECRET';
$cases = [
    [
        'name' => 'check_minimal',
        'key' => 'WB-UMAN-1a2b3c4dQF',
        'timestamp' => 1789000000,
        'nonce' => '0123456789abcdef0123456789abcdef',
        'body' => '{"jsonrpc":"2.0","method":"call","params":{"key":"WB-UMAN-1a2b3c4dQF"}}',
    ],
    [
        'name' => 'check_with_instance',
        'key' => 'WB-UMAN-1a2b3c4dQF',
        'timestamp' => 1789000123,
        'nonce' => 'fedcba9876543210fedcba9876543210',
        'body' => '{"jsonrpc":"2.0","method":"call","params":{"key":"WB-UMAN-1a2b3c4dQF","domain":"wissen-api.wissen-beratung.de","db_uuid":"9f1c2d3e4f5a6b7c"}}',
    ],
    [
        'name' => 'umlauts_and_slashes',
        'key' => 'WB-BITW-00ff11eeAB',
        'timestamp' => 1789000456,
        'nonce' => 'a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6',
        'body' => '{"jsonrpc":"2.0","method":"call","params":{"company_name":"Müller & Söhne GmbH","notes":"Straße/Weg — Test"}}',
    ],
    [
        'name' => 'empty_params',
        'key' => 'WB-TELE-abcdef01GH',
        'timestamp' => 1789000789,
        'nonce' => 'ffffffffffffffffffffffffffffffff',
        'body' => '{"jsonrpc":"2.0","method":"call","params":{}}',
    ],
];

$vectors = [
    'scheme' => Signature::SCHEME,
    'secret' => $secret,
    'note' => 'Generiert von generate_vectors.php. Testdaten, kein echtes Secret.',
    'cases' => [],
];

foreach ($cases as $case) {
    $case['body_sha256'] = hash('sha256', $case['body']);
    $case['signature_base'] = Signature::base(
        $case['key'],
        $case['timestamp'],
        $case['nonce'],
        $case['body']
    );
    $case['signature'] = Signature::compute(
        $secret,
        $case['key'],
        $case['timestamp'],
        $case['nonce'],
        $case['body']
    );
    $vectors['cases'][] = $case;
}

echo json_encode($vectors, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE), "\n";
