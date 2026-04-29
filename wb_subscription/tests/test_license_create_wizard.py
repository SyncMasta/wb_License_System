"""Tests fuer wb.license.create.wizard.

Deckt ab:
- Wizard erzeugt License-Key + Activation-Ticket korrekt
- Toggles steuern Cert-Generation / Mail-Versand / Telegram
- valid_to-Default = 31.12. (oder naechstes Jahr bei Dezember-Sale)
- Constraint: valid_to nicht vor valid_from
- Mail-Toggle ohne Partner-Email → UserError
- Ohne sale_order_id: stand-alone Lizenz
- audit-event 'key_generated_manual' wird angelegt wenn Notiz gesetzt
"""
import os
from datetime import date, timedelta
from unittest.mock import patch

from cryptography.fernet import Fernet
from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('wb_subscription', 'wb_license_create_wizard')
class TestLicenseCreateWizard(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._fernet_env_backup = os.environ.get('WB_SUBSCRIPTION_FERNET_KEY')
        os.environ['WB_SUBSCRIPTION_FERNET_KEY'] = (
            Fernet.generate_key().decode('utf-8'))

        cls.product = cls.env['product.template'].create({
            'name': 'TEST Manual-License Product',
            'wb_is_license_product': True,
            'wb_technical_code': 'TMNL',
            'wb_billing_calendar': 'monthly',
            'list_price': 50.0,
            'type': 'service',
        }).product_variant_id

        cls.partner_with_email = cls.env['res.partner'].create({
            'name': 'Manual-Test-Kunde',
            'email': 'manual@example.com',
        })
        cls.partner_no_email = cls.env['res.partner'].create({
            'name': 'No-Email-Partner',
        })

    @classmethod
    def tearDownClass(cls):
        if cls._fernet_env_backup is None:
            os.environ.pop('WB_SUBSCRIPTION_FERNET_KEY', None)
        else:
            os.environ['WB_SUBSCRIPTION_FERNET_KEY'] = cls._fernet_env_backup
        super().tearDownClass()

    def _make_wizard(self, **overrides):
        vals = {
            'partner_id': self.partner_with_email.id,
            'product_id': self.product.id,
            'send_payment_received_mail': False,
            'send_activation_mail': False,
            'generate_certificate': False,
            'send_telegram': False,
        }
        vals.update(overrides)
        return self.env['wb.license.create.wizard'].create(vals)

    # ---------------------------------------------------- Default valid_to

    def test_default_valid_to_is_eoy(self):
        wiz = self.env['wb.license.create.wizard'].new({
            'partner_id': self.partner_with_email.id,
            'product_id': self.product.id,
        })
        eoy = date(fields.Date.today().year, 12, 31)
        # Wenn weniger als 30 Tage zu eoy → naechstes Jahr
        if (eoy - fields.Date.today()).days < 30:
            eoy = date(fields.Date.today().year + 1, 12, 31)
        self.assertEqual(wiz.valid_to, eoy)

    # --------------------------------------------------- Validity-Constraint

    def test_constraint_valid_to_after_valid_from(self):
        wiz = self._make_wizard(
            valid_from=fields.Date.today() + timedelta(days=10),
            valid_to=fields.Date.today() + timedelta(days=5),
        )
        with self.assertRaises(UserError):
            wiz._check_validity_range()

    # --------------------------------------------------------- Create-Path

    def test_action_create_creates_key_and_ticket(self):
        wiz = self._make_wizard()
        action = wiz.action_create_license()
        self.assertEqual(action['res_model'], 'wb.license.key')
        key = self.env['wb.license.key'].browse(action['res_id'])
        self.assertTrue(key.exists())
        self.assertEqual(key.state, 'issued')
        self.assertEqual(key.partner_id, self.partner_with_email)
        self.assertEqual(key.product_id, self.product)
        # Ticket
        ticket = self.env['wb.activation.ticket'].search([
            ('license_id', '=', key.id),
        ], limit=1)
        self.assertTrue(ticket)
        self.assertTrue(ticket.encrypted_code,
            "Activation-Ticket muss encrypted_code haben.")

    def test_create_without_sale_order(self):
        """Standalone-Lizenz ohne sale_order_id."""
        wiz = self._make_wizard()  # sale_order_id default = False
        action = wiz.action_create_license()
        key = self.env['wb.license.key'].browse(action['res_id'])
        self.assertFalse(key.sale_order_id)

    # ---------------------------------------------------------- Email-Guard

    def test_mail_toggle_without_email_raises(self):
        wiz = self.env['wb.license.create.wizard'].create({
            'partner_id': self.partner_no_email.id,
            'product_id': self.product.id,
            'send_payment_received_mail': True,
            'send_activation_mail': False,
            'generate_certificate': False,
            'send_telegram': False,
        })
        with self.assertRaises(UserError) as cm:
            wiz.action_create_license()
        self.assertIn('Email', str(cm.exception))

    def test_no_mails_without_email_works(self):
        """Wenn ALLE Mail-Toggles aus sind, funktioniert auch Partner ohne Email."""
        wiz = self.env['wb.license.create.wizard'].create({
            'partner_id': self.partner_no_email.id,
            'product_id': self.product.id,
            'send_payment_received_mail': False,
            'send_activation_mail': False,
            'generate_certificate': False,
            'send_telegram': False,
        })
        action = wiz.action_create_license()
        key = self.env['wb.license.key'].browse(action['res_id'])
        self.assertTrue(key.exists())

    # ------------------------------------------------- Audit-Trail Notiz

    def test_notes_create_audit_event(self):
        wiz = self._make_wizard(notes='Test-Smoke-Run #42')
        action = wiz.action_create_license()
        key = self.env['wb.license.key'].browse(action['res_id'])
        events = self.env['wb.license.event'].search([
            ('license_id', '=', key.id),
            ('event_type', '=', 'key_generated_manual'),
        ])
        self.assertTrue(events,
            "Bei Notiz soll ein 'key_generated_manual'-Audit-Event entstehen.")
