# -*- coding: utf-8 -*-
"""Sprint 2 Security-Tests fuer wb_subscription.

* L-H2: Fernet-Key-Rotation via WB_SUBSCRIPTION_FERNET_KEY_HISTORY
* B-M1: redact_secrets() vor wb.license.event.details persist
"""
import os

from cryptography.fernet import Fernet, InvalidToken

from odoo.tests import TransactionCase, tagged

from ..models._redact import redact_secrets


@tagged('wb_subscription', 'security_regression')
class TestRedactSecretsSubscription(TransactionCase):
    """B-M1: details werden vor dem Persist saniert."""

    def test_activation_code_pattern_redacted(self):
        out = redact_secrets('activation_code=ABCDE-FGHIJ-KLMNO-PQRST-UVWXY&domain=foo')
        self.assertIn('activation_code=[REDACTED]', out)
        self.assertNotIn('ABCDE-FGHIJ', out)

    def test_json_secret_redacted(self):
        out = redact_secrets('{"client_secret": "supersecret123"}')
        self.assertIn('[REDACTED]', out)
        self.assertNotIn('supersecret123', out)

    def test_event_log_persist_redacts_details(self):
        Event = self.env['wb.license.event']
        details = {'attempt_payload': 'activation_code=SECRET-CODE-123-456-789'}
        rec = Event.log_event(False, 'activation_failed', details=details)
        self.assertIn('[REDACTED]', rec.details or '')
        self.assertNotIn('SECRET-CODE-123', rec.details or '')


@tagged('wb_subscription', 'security_regression')
class TestFernetRotation(TransactionCase):
    """L-H2: encrypt mit aktuellem Key, decrypt mit current ODER history."""

    def setUp(self):
        super().setUp()
        # Originale ENV-Werte sichern und am Ende wiederherstellen.
        self._old_cur = os.environ.get('WB_SUBSCRIPTION_FERNET_KEY')
        self._old_his = os.environ.get('WB_SUBSCRIPTION_FERNET_KEY_HISTORY')

    def tearDown(self):
        if self._old_cur is None:
            os.environ.pop('WB_SUBSCRIPTION_FERNET_KEY', None)
        else:
            os.environ['WB_SUBSCRIPTION_FERNET_KEY'] = self._old_cur
        if self._old_his is None:
            os.environ.pop('WB_SUBSCRIPTION_FERNET_KEY_HISTORY', None)
        else:
            os.environ['WB_SUBSCRIPTION_FERNET_KEY_HISTORY'] = self._old_his
        super().tearDown()

    def test_round_trip_with_single_env_key(self):
        Gen = self.env['wb.key.generator']
        os.environ['WB_SUBSCRIPTION_FERNET_KEY'] = Fernet.generate_key().decode()
        os.environ.pop('WB_SUBSCRIPTION_FERNET_KEY_HISTORY', None)
        cipher = Gen.encrypt_code('ABCDE-FGHIJ-KLMNO-PQRST-UVWXY')
        self.assertEqual(Gen.decrypt_code(cipher),
                         'ABCDE-FGHIJ-KLMNO-PQRST-UVWXY')

    def test_legacy_decrypt_via_history(self):
        Gen = self.env['wb.key.generator']
        old_key = Fernet.generate_key().decode()
        new_key = Fernet.generate_key().decode()

        # Schritt 1: alter Key ist current — verschluesseln
        os.environ['WB_SUBSCRIPTION_FERNET_KEY'] = old_key
        os.environ.pop('WB_SUBSCRIPTION_FERNET_KEY_HISTORY', None)
        legacy_cipher = Gen.encrypt_code('LEGACY-CODE-12345')

        # Schritt 2: Roll-out — neuer Key current, alter in History
        os.environ['WB_SUBSCRIPTION_FERNET_KEY'] = new_key
        os.environ['WB_SUBSCRIPTION_FERNET_KEY_HISTORY'] = old_key
        self.assertEqual(Gen.decrypt_code(legacy_cipher), 'LEGACY-CODE-12345',
                         "Tickets vor Rotation muessen lesbar bleiben")

        # Schritt 3: ein mit neuem Key encryptetes Cipher ist mit altem
        # Key allein NICHT entschluesselbar
        new_cipher = Gen.encrypt_code('NEW-CODE-99999')
        with self.assertRaises(InvalidToken):
            Fernet(old_key.encode()).decrypt(new_cipher)

    def test_decrypt_fails_when_key_dropped_from_history(self):
        Gen = self.env['wb.key.generator']
        from odoo.exceptions import UserError

        old_key = Fernet.generate_key().decode()
        os.environ['WB_SUBSCRIPTION_FERNET_KEY'] = old_key
        os.environ.pop('WB_SUBSCRIPTION_FERNET_KEY_HISTORY', None)
        legacy_cipher = Gen.encrypt_code('CODE-TO-LOSE')

        # Kein einziger der beiden Keys passt mehr
        os.environ['WB_SUBSCRIPTION_FERNET_KEY'] = Fernet.generate_key().decode()
        os.environ.pop('WB_SUBSCRIPTION_FERNET_KEY_HISTORY', None)
        with self.assertRaises(UserError):
            Gen.decrypt_code(legacy_cipher)
