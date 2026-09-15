"""Tests für die HMAC-Signatur der Client-Requests.

Zwei Ebenen:

1. **Interop.** Die Vektoren in ``wb_license_client_php/tests/interop/vectors.json``
   sind von der PHP-Implementierung erzeugt worden. Weicht der Server davon ab,
   passen Client und Server nicht mehr zusammen — genau der Fehler, der im
   Betrieb erst auffällt, wenn niemand mehr durchkommt.
2. **Verhalten.** Secret-Erzeugung, Rotation, Verifikation und die
   Ablehnungsfälle.

Die Vektoren-Datei wird bewusst NICHT im Test neu erzeugt. Sie ist eine
Regressionsbremse: ändert jemand das Schema, schlägt der Test fehl, statt
still eine inkompatible Version auszurollen.
"""

import json
import os
from datetime import timedelta

from cryptography.fernet import Fernet
from odoo import fields
from odoo.tests import TransactionCase, tagged

VECTORS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'wb_license_client_php', 'tests', 'interop', 'vectors.json',
)


@tagged('wb_subscription', 'wb_hmac')
class TestHmacSignature(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.gen = cls.env['wb.key.generator'].sudo()

    # ------------------------------------------------ Interop mit PHP

    def _load_vectors(self):
        if not os.path.exists(VECTORS_PATH):
            self.skipTest(
                "Interop-Vektoren nicht gefunden (%s) — der PHP-Client ist in "
                "diesem Deployment nicht mit ausgerollt." % VECTORS_PATH)
        with open(VECTORS_PATH, encoding='utf-8') as fh:
            return json.load(fh)

    def test_signature_base_matches_php(self):
        """Der signierte String ist auf beiden Seiten byte-identisch."""
        vectors = self._load_vectors()
        for case in vectors['cases']:
            with self.subTest(case=case['name']):
                self.assertEqual(
                    self.gen.build_signature_base(
                        case['key'], case['timestamp'], case['nonce'], case['body']),
                    case['signature_base'],
                )

    def test_signature_matches_php(self):
        """Die berechnete Signatur stimmt mit der PHP-Implementierung überein."""
        vectors = self._load_vectors()
        for case in vectors['cases']:
            with self.subTest(case=case['name']):
                self.assertEqual(
                    self.gen.compute_request_signature(
                        vectors['secret'], case['key'], case['timestamp'],
                        case['nonce'], case['body']),
                    case['signature'],
                )

    def test_verify_accepts_php_signature_with_and_without_prefix(self):
        vectors = self._load_vectors()
        case = vectors['cases'][0]
        for provided in (case['signature'], 'v1=' + case['signature']):
            self.assertTrue(self.gen.verify_request_signature(
                vectors['secret'], case['key'], case['timestamp'],
                case['nonce'], case['body'], provided))

    def test_verify_rejects_tampered_body(self):
        """Ein verändertes Byte im Body macht die Signatur ungültig."""
        vectors = self._load_vectors()
        case = vectors['cases'][0]
        self.assertFalse(self.gen.verify_request_signature(
            vectors['secret'], case['key'], case['timestamp'],
            case['nonce'], case['body'] + ' ', case['signature']))

    def test_verify_rejects_wrong_secret(self):
        vectors = self._load_vectors()
        case = vectors['cases'][0]
        self.assertFalse(self.gen.verify_request_signature(
            'WBS-' + 'x' * 43, case['key'], case['timestamp'],
            case['nonce'], case['body'], case['signature']))

    def test_verify_rejects_replayed_nonce_value(self):
        """Nonce ist Teil des signierten Strings — sie lässt sich nicht tauschen."""
        vectors = self._load_vectors()
        case = vectors['cases'][0]
        self.assertFalse(self.gen.verify_request_signature(
            vectors['secret'], case['key'], case['timestamp'],
            'andere-nonce', case['body'], case['signature']))

    # ------------------------------------------------ Secret-Handling

    def test_generate_api_secret_format(self):
        for _ in range(20):
            secret = self.gen.generate_api_secret()
            self.assertTrue(self.gen.validate_api_secret_format(secret), secret)

    def test_generate_api_secret_is_unique(self):
        secrets_seen = {self.gen.generate_api_secret() for _ in range(200)}
        self.assertEqual(len(secrets_seen), 200)

    def test_verify_without_signature_or_secret_is_false(self):
        self.assertFalse(self.gen.verify_request_signature(
            'WBS-' + 'a' * 43, 'WB-TEST-0000000AA', 1, 'n', b'{}', ''))
        self.assertFalse(self.gen.verify_request_signature(
            '', 'WB-TEST-0000000AA', 1, 'n', b'{}', 'deadbeef'))

    def test_signature_accepts_bytes_and_str_body(self):
        """Body darf als bytes oder str kommen — gleiches Ergebnis."""
        secret = self.gen.generate_api_secret()
        body = '{"params":{"a":1}}'
        self.assertEqual(
            self.gen.compute_request_signature(secret, 'K', 1, 'n', body),
            self.gen.compute_request_signature(secret, 'K', 1, 'n', body.encode('utf-8')),
        )


@tagged('wb_subscription', 'wb_hmac')
class TestLicenseKeyApiSecret(TransactionCase):
    """Secret-Lebenszyklus am Lizenzschlüssel."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._fernet_env_backup = os.environ.get('WB_SUBSCRIPTION_FERNET_KEY')
        os.environ['WB_SUBSCRIPTION_FERNET_KEY'] = Fernet.generate_key().decode('utf-8')

        template = cls.env['product.template'].create({
            'name': 'TEST HMAC-Produkt',
            'wb_is_license_product': True,
            'wb_technical_code': 'HMAC',
            'wb_instance_limit': 1,
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'HMAC Testkunde GmbH',
            'email': 'hmac@example.com',
        })
        cls.license = cls.env['wb.license.key'].create({
            'product_id': template.product_variant_id.id,
            'partner_id': cls.partner.id,
            'state': 'issued',
            'valid_from': fields.Date.today(),
            'valid_to': fields.Date.today() + timedelta(days=365),
        })

    @classmethod
    def tearDownClass(cls):
        if cls._fernet_env_backup is None:
            os.environ.pop('WB_SUBSCRIPTION_FERNET_KEY', None)
        else:
            os.environ['WB_SUBSCRIPTION_FERNET_KEY'] = cls._fernet_env_backup
        super().tearDownClass()

    def test_no_secret_by_default(self):
        """Bestandsschlüssel haben kein Secret — sonst wäre der Rollout ein Bruch."""
        self.assertFalse(self.license.has_api_secret)
        self.assertFalse(self.license.api_secret_hint)
        self.assertIsNone(self.license._get_api_secret())

    def test_generate_and_roundtrip(self):
        self.license.action_generate_api_secret()
        self.assertTrue(self.license.has_api_secret)
        secret = self.license._get_api_secret()
        self.assertTrue(self.env['wb.key.generator'].sudo()
                        .validate_api_secret_format(secret))
        self.assertEqual(self.license.api_secret_hint, secret[-4:])
        self.assertTrue(self.license.api_secret_issued_at)

    def test_rotation_invalidates_previous_secret(self):
        self.license.action_generate_api_secret()
        first = self.license._get_api_secret()
        self.license.action_generate_api_secret()
        second = self.license._get_api_secret()
        self.assertNotEqual(first, second)

    def test_revoke_clears_secret(self):
        self.license.action_generate_api_secret()
        self.license.action_revoke_api_secret()
        self.assertFalse(self.license.has_api_secret)
        self.assertIsNone(self.license._get_api_secret())

    def test_secret_is_not_written_to_chatter(self):
        """Das Secret darf nirgends im Klartext landen — auch nicht im Chatter."""
        self.license.action_generate_api_secret()
        secret = self.license._get_api_secret()
        bodies = ' '.join(self.license.message_ids.mapped('body') or [])
        self.assertNotIn(secret, bodies)

    def test_events_are_logged(self):
        self.license.action_generate_api_secret()
        self.license.action_revoke_api_secret()
        types_logged = self.license.event_ids.mapped('event_type')
        self.assertIn('api_secret_issued', types_logged)
        self.assertIn('api_secret_revoked', types_logged)
        details = ' '.join(self.license.event_ids.mapped('details') or [])
        self.assertNotIn(self.license.api_secret_hint or 'WBS-', details)
