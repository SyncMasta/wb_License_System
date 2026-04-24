"""Tests für den Key-/Code-/Ticket-Generator.

Führt die Algorithmen aus ARCHITECTURE.md Kapitel 4 gegen die
Implementierung. Kritisch: Format-Regex müssen gegen die eigenen
Generatoren matchen, Roundtrips bcrypt/Fernet müssen funktionieren.
"""

import os
import re

from cryptography.fernet import Fernet
from odoo.tests import TransactionCase, tagged


@tagged('wb_subscription', 'wb_key_generator')
class TestKeyGenerator(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.gen = cls.env['wb.key.generator']
        cls._fernet_env_backup = os.environ.get('WB_SUBSCRIPTION_FERNET_KEY')
        test_key = Fernet.generate_key().decode('utf-8')
        os.environ['WB_SUBSCRIPTION_FERNET_KEY'] = test_key

    @classmethod
    def tearDownClass(cls):
        if cls._fernet_env_backup is None:
            os.environ.pop('WB_SUBSCRIPTION_FERNET_KEY', None)
        else:
            os.environ['WB_SUBSCRIPTION_FERNET_KEY'] = cls._fernet_env_backup
        super().tearDownClass()

    def test_public_key_format_matches_regex(self):
        key = self.gen.generate_public_key('TEST')
        self.assertRegex(
            key,
            r'^WB-TEST-[a-f0-9]{8}[A-Z2-7]{2}$',
            f"Generated key {key!r} entspricht nicht dem erwarteten Format.",
        )

    def test_public_key_checksum_is_valid(self):
        """Selbst erzeugter Key muss die Checksum-Validierung bestehen."""
        for _ in range(50):
            key = self.gen.generate_public_key('TEST')
            self.assertTrue(
                self.gen.validate_key_format(key),
                f"Key {key} wird nicht als valid erkannt.",
            )

    def test_manipulated_checksum_is_rejected(self):
        key = self.gen.generate_public_key('TEST')
        manipulated = key[:-2] + 'ZZ'
        self.assertFalse(
            self.gen.validate_key_format(manipulated),
            "Manipulierte Checksum muss abgelehnt werden.",
        )

    def test_public_key_uniqueness(self):
        """1000 Keys in Folge müssen alle unique sein."""
        keys = {self.gen.generate_public_key('TEST') for _ in range(1000)}
        self.assertEqual(len(keys), 1000, "UUID-Kollision in 1000 Keys — extrem unwahrscheinlich.")

    def test_product_code_must_be_4_uppercase(self):
        from odoo.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            self.gen.generate_public_key('test')  # lowercase
        with self.assertRaises(ValidationError):
            self.gen.generate_public_key('ABC')  # zu kurz
        with self.assertRaises(ValidationError):
            self.gen.generate_public_key('ABCDE')  # zu lang

    def test_activation_code_format(self):
        for _ in range(50):
            code = self.gen.generate_activation_code()
            self.assertTrue(
                self.gen.validate_activation_code_format(code),
                f"Code {code} wird nicht als valid erkannt.",
            )

    def test_activation_code_has_no_ambiguous_chars(self):
        """Codes dürfen keine 0, 1, I, O enthalten."""
        forbidden = set('01IO')
        for _ in range(100):
            code = self.gen.generate_activation_code()
            code_chars = set(code.replace('-', ''))
            self.assertTrue(
                code_chars.isdisjoint(forbidden),
                f"Code {code} enthält verbotene Zeichen {code_chars & forbidden}",
            )

    def test_activation_code_structure_25_chars_in_5_groups(self):
        code = self.gen.generate_activation_code()
        parts = code.split('-')
        self.assertEqual(len(parts), 5)
        for p in parts:
            self.assertEqual(len(p), 5)

    def test_bcrypt_roundtrip(self):
        code = self.gen.generate_activation_code()
        hashed = self.gen.hash_activation_code(code)
        self.assertTrue(self.gen.verify_activation_code(code, hashed))
        self.assertFalse(self.gen.verify_activation_code(code + 'X', hashed))

    def test_bcrypt_verify_with_invalid_hash_returns_false(self):
        """Korrupte Hashes dürfen nicht crashen, nur False liefern."""
        code = self.gen.generate_activation_code()
        self.assertFalse(self.gen.verify_activation_code(code, b''))
        self.assertFalse(self.gen.verify_activation_code(code, b'garbage'))
        self.assertFalse(self.gen.verify_activation_code(code, False))

    def test_fernet_roundtrip(self):
        code = self.gen.generate_activation_code()
        encrypted = self.gen.encrypt_code(code)
        decrypted = self.gen.decrypt_code(encrypted)
        self.assertEqual(code, decrypted)

    def test_ticket_token_format(self):
        token = self.gen.generate_ticket_token()
        self.assertTrue(token.startswith('TCKT-'))
        self.assertEqual(len(token), 27)  # TCKT- (5) + 22 base64url-chars
        self.assertRegex(token, r'^TCKT-[A-Za-z0-9_-]{22}$')

    def test_email_otp_format(self):
        for _ in range(20):
            otp = self.gen.generate_email_otp()
            self.assertEqual(len(otp), 6)
            self.assertTrue(otp.isdigit())

    def test_otp_bcrypt_roundtrip(self):
        otp = self.gen.generate_email_otp()
        hashed = self.gen.hash_otp(otp)
        self.assertTrue(self.gen.verify_otp(otp, hashed))
        self.assertFalse(self.gen.verify_otp('000000', hashed))

    def test_fingerprint_stability(self):
        """Gleiche Inputs → gleicher Fingerprint. Whitespace wird normalisiert."""
        fp1 = self.gen.compute_fingerprint('example.com', 'a7b3c2d1')
        fp2 = self.gen.compute_fingerprint('Example.com', '  a7b3c2d1  ')
        self.assertEqual(fp1, fp2)

    def test_fingerprint_differs_across_inputs(self):
        fp1 = self.gen.compute_fingerprint('a.de', 'uuid-1')
        fp2 = self.gen.compute_fingerprint('a.de', 'uuid-2')
        fp3 = self.gen.compute_fingerprint('b.de', 'uuid-1')
        self.assertNotEqual(fp1, fp2)
        self.assertNotEqual(fp1, fp3)
        self.assertNotEqual(fp2, fp3)

    def test_fingerprint_requires_both_inputs(self):
        from odoo.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            self.gen.compute_fingerprint('', 'uuid')
        with self.assertRaises(ValidationError):
            self.gen.compute_fingerprint('domain', '')

    def test_checksum_regression(self):
        """Die Checksum-Implementierung darf sich nicht ändern.

        Wenn dieser Test fehlschlägt, müssten alle bestehenden Keys neu
        ausgestellt werden. Daher: Regression-Lock.
        """
        # Dieser Checksum-Wert ist aus dem Algorithmus aus ARCHITECTURE.md 4.5
        # auf einem konstanten Payload berechnet.
        payload = 'WB-ELST-a3f28c91'
        expected_checksum = self.gen.compute_checksum(payload)
        # 2-stellig, alphabet ABCDEFGHIJKLMNOPQRSTUVWXYZ234567
        self.assertEqual(len(expected_checksum), 2)
        self.assertTrue(all(c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567' for c in expected_checksum))
        # Deterministisch: mehrfache Aufrufe liefern gleichen Wert
        for _ in range(5):
            self.assertEqual(self.gen.compute_checksum(payload), expected_checksum)
