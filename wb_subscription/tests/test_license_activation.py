"""Tests für den Activation-Flow.

Sicherheits-Kritisch: Die Tests stellen sicher, dass:
- Falsche Codes abgelehnt werden
- Doppel-Activations geblockt sind ("Key burned")
- Abgelaufene Codes erkannt werden
- Activation-Code im Klartext NIEMALS persistiert wird
"""

import os
from datetime import timedelta

from cryptography.fernet import Fernet
from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('wb_subscription', 'wb_license_activation')
class TestLicenseActivation(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._fernet_env_backup = os.environ.get('WB_SUBSCRIPTION_FERNET_KEY')
        os.environ['WB_SUBSCRIPTION_FERNET_KEY'] = Fernet.generate_key().decode('utf-8')
        cls.gen = cls.env['wb.key.generator']

        cls.product_template = cls.env['product.template'].create({
            'name': 'TEST Lizenz-Produkt',
            'wb_is_license_product': True,
            'wb_technical_code': 'TEST',
            'wb_instance_limit': 1,
            'wb_activation_grace_days': 90,
        })
        cls.product = cls.product_template.product_variant_id

        cls.partner = cls.env['res.partner'].create({
            'name': 'Test-Kunde GmbH',
            'email': 'test@example.com',
        })

    @classmethod
    def tearDownClass(cls):
        if cls._fernet_env_backup is None:
            os.environ.pop('WB_SUBSCRIPTION_FERNET_KEY', None)
        else:
            os.environ['WB_SUBSCRIPTION_FERNET_KEY'] = cls._fernet_env_backup
        super().tearDownClass()

    def _make_issued_license(self):
        code = self.gen.generate_activation_code()
        license = self.env['wb.license.key'].create({
            'product_id': self.product.id,
            'partner_id': self.partner.id,
            'state': 'issued',
            'valid_from': fields.Date.today(),
            'valid_to': fields.Date.today() + timedelta(days=365),
            'activation_hash': self.gen.hash_activation_code(code),
            'activation_expires_at': fields.Datetime.now() + timedelta(days=90),
        })
        return license, code

    def test_activate_correct_code_succeeds(self):
        license, code = self._make_issued_license()
        result = license.activate_with_code(
            code, 'kunde.example.com', 'db-uuid-1234',
            ip='1.2.3.4', user_agent='test',
        )
        self.assertEqual(result['status'], 'ok')
        license.invalidate_recordset()
        self.assertEqual(license.state, 'active')
        self.assertEqual(license.bound_domain, 'kunde.example.com')
        self.assertEqual(license.bound_db_uuid, 'db-uuid-1234')
        self.assertTrue(license.activated_at)
        self.assertFalse(license.activation_hash, "activation_hash muss nach Erfolg gelöscht werden ('burned').")

    def test_activate_wrong_code_fails(self):
        license, _correct_code = self._make_issued_license()
        result = license.activate_with_code(
            'AAAAA-BBBBB-CCCCC-DDDDD-EEEEE', 'kunde.example.com', 'db-uuid-1234',
        )
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['error'], 'WRONG_CODE')
        license.invalidate_recordset()
        self.assertEqual(license.state, 'issued')
        self.assertFalse(license.activated_at)
        self.assertTrue(license.activation_hash, "Hash darf bei Fehlversuch nicht entwertet werden.")

    def test_activate_already_activated_fails(self):
        license, code = self._make_issued_license()
        license.activate_with_code(code, 'kunde.example.com', 'db-uuid-1234')
        result = license.activate_with_code(
            code, 'andere-domain.de', 'db-uuid-9999',
        )
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['error'], 'ALREADY_ACTIVATED')

    def test_activate_expired_code_fails(self):
        license, code = self._make_issued_license()
        license.activation_expires_at = fields.Datetime.now() - timedelta(days=1)
        result = license.activate_with_code(code, 'kunde.example.com', 'db-uuid-1234')
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['error'], 'ACTIVATION_EXPIRED')

    def test_failed_activation_logs_event(self):
        license, _ = self._make_issued_license()
        license.activate_with_code(
            'AAAAA-BBBBB-CCCCC-DDDDD-EEEEE', 'kunde.example.com', 'db-uuid-1234',
            ip='5.6.7.8',
        )
        events = self.env['wb.license.event'].search([
            ('license_id', '=', license.id),
            ('event_type', '=', 'activation_failed'),
        ])
        self.assertTrue(events)
        self.assertEqual(events[0].ip_address, '5.6.7.8')

    def test_successful_activation_logs_event_with_fingerprint(self):
        license, code = self._make_issued_license()
        license.activate_with_code(code, 'kunde.example.com', 'db-uuid-1234')
        license.invalidate_recordset()
        self.assertTrue(license.activated_fingerprint)
        events = self.env['wb.license.event'].search([
            ('license_id', '=', license.id),
            ('event_type', '=', 'activation'),
        ])
        self.assertTrue(events)

    def test_activation_code_never_in_db_field(self):
        """Klartext-Code darf NICHT als Klartext-Feld gespeichert werden."""
        license, code = self._make_issued_license()
        license_dict = license.read()[0]
        for field_name, value in license_dict.items():
            if isinstance(value, str):
                self.assertNotIn(code, value,
                                 f"Klartext-Code darf nicht in Feld {field_name} stehen!")

    def test_revoke_changes_state(self):
        license, code = self._make_issued_license()
        license.activate_with_code(code, 'd.example.com', 'uuid-1')
        license.action_revoke(reason='Test-Revoke')
        license.invalidate_recordset()
        self.assertEqual(license.state, 'revoked')

    def test_revoke_logs_event(self):
        license, code = self._make_issued_license()
        license.activate_with_code(code, 'd.example.com', 'uuid-1')
        license.action_revoke(reason='Vertragsbruch')
        events = self.env['wb.license.event'].search([
            ('license_id', '=', license.id),
            ('event_type', '=', 'revoked'),
        ])
        self.assertTrue(events)

    def test_renew_resets_notification_flags(self):
        license, code = self._make_issued_license()
        license.activate_with_code(code, 'd.example.com', 'uuid-1')
        license.notified_renewal_60d_at = fields.Datetime.now()
        license.notified_renewal_30d_at = fields.Datetime.now()
        license.action_renew(fields.Date.today() + timedelta(days=730))
        self.assertFalse(license.notified_renewal_60d_at)
        self.assertFalse(license.notified_renewal_30d_at)
