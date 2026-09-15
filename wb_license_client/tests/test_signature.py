"""Tests für die HMAC-Signatur der Client-Requests.

Der Client ist die dritte Implementierung desselben Schemas (neben
wb_subscription serverseitig und dem PHP-Paket). Genau da entstehen
Abweichungen, die niemand merkt, bis der Server auf ``required`` steht
und keine Instanz mehr durchkommt.

Deshalb werden hier dieselben Vektoren geprüft wie in
``wb_subscription/tests/test_hmac_signature.py``.
"""

import hashlib
import hmac
import json
import os

from odoo.tests import TransactionCase, tagged

VECTORS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'wb_license_client_php', 'tests', 'interop', 'vectors.json',
)


@tagged('wb_license_client', 'wb_hmac')
class TestClientSignature(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.client = cls.env['wb.license.client']

    def _vectors(self):
        if not os.path.exists(VECTORS_PATH):
            self.skipTest("Interop-Vektoren nicht verfügbar: %s" % VECTORS_PATH)
        with open(VECTORS_PATH, encoding='utf-8') as fh:
            return json.load(fh)

    def test_headers_match_interop_vectors(self):
        """Die Signatur des Clients deckt sich mit den PHP-Vektoren.

        Timestamp und Nonce erzeugt der Client selbst, also wird mit seinen
        eigenen Werten nachgerechnet — geprüft wird der Algorithmus, nicht
        eine eingefrorene Zufallszahl.
        """
        vectors = self._vectors()
        secret = vectors['secret']
        for case in vectors['cases']:
            with self.subTest(case=case['name']):
                body = case['body'].encode('utf-8')
                headers = self.client._signature_headers(secret, case['key'], body)
                base = '\n'.join([
                    'WB-HMAC-V1', case['key'], headers['X-WB-Timestamp'],
                    headers['X-WB-Nonce'], hashlib.sha256(body).hexdigest(),
                ])
                expected = hmac.new(
                    secret.encode('utf-8'), base.encode('utf-8'), hashlib.sha256,
                ).hexdigest()
                self.assertEqual(headers['X-WB-Signature'], 'v1=' + expected)

    def test_header_set_is_complete(self):
        headers = self.client._signature_headers(
            'WBS-' + 'a' * 43, 'WB-TEST-0000000AA', b'{}')
        self.assertEqual(
            set(headers),
            {'X-WB-Key', 'X-WB-Timestamp', 'X-WB-Nonce', 'X-WB-Signature'},
        )
        self.assertTrue(headers['X-WB-Signature'].startswith('v1='))

    def test_nonce_is_unique_per_request(self):
        """Der Server sperrt jede Nonce — eine wiederholte wäre ein Selbstblock."""
        nonces = {
            self.client._signature_headers(
                'WBS-' + 'a' * 43, 'WB-TEST-0000000AA', b'{}')['X-WB-Nonce']
            for _ in range(200)
        }
        self.assertEqual(len(nonces), 200)

    def test_signature_covers_body(self):
        """Ein verändertes Byte im Body ergibt eine andere Signatur."""
        secret = 'WBS-' + 'a' * 43
        first = self.client._signature_headers(secret, 'WB-TEST-0000000AA', b'{"a":1}')
        base_same_nonce = '\n'.join([
            'WB-HMAC-V1', 'WB-TEST-0000000AA', first['X-WB-Timestamp'],
            first['X-WB-Nonce'], hashlib.sha256(b'{"a":2}').hexdigest(),
        ])
        other = hmac.new(
            secret.encode('utf-8'), base_same_nonce.encode('utf-8'), hashlib.sha256,
        ).hexdigest()
        self.assertNotEqual(first['X-WB-Signature'], 'v1=' + other)

    def test_secret_storage_roundtrip(self):
        self.assertFalse(self.client._get_stored_secret('TEST'))
        self.client._store_secret('TEST', 'WBS-' + 'b' * 43)
        self.assertEqual(self.client._get_stored_secret('TEST'), 'WBS-' + 'b' * 43)
        self.client._store_secret('TEST', '')
        self.assertFalse(self.client._get_stored_secret('TEST'))
