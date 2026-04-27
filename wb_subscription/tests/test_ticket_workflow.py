"""Tests für den Activation-Ticket-Workflow.

Deckt ab:
- IP-Binding-Verhalten (DECISION #48c)
- OTP-Versuchszähler
- Code-Reveal nach Verify
- Burn nach Consume
- Cleanup-Cron
"""

import os
from datetime import timedelta

from cryptography.fernet import Fernet

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('wb_subscription', 'wb_ticket_workflow')
class TestTicketWorkflow(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._fernet_env_backup = os.environ.get('WB_SUBSCRIPTION_FERNET_KEY')
        os.environ['WB_SUBSCRIPTION_FERNET_KEY'] = Fernet.generate_key().decode('utf-8')
        cls.gen = cls.env['wb.key.generator']

        cls.product_template = cls.env['product.template'].create({
            'name': 'Ticket-Test-Produkt',
            'wb_is_license_product': True,
            'wb_technical_code': 'TKTT',
        })
        cls.product = cls.product_template.product_variant_id
        cls.partner = cls.env['res.partner'].create({
            'name': 'Ticket-Test',
            'email': 'ticket@example.com',
        })

    @classmethod
    def tearDownClass(cls):
        if cls._fernet_env_backup is None:
            os.environ.pop('WB_SUBSCRIPTION_FERNET_KEY', None)
        else:
            os.environ['WB_SUBSCRIPTION_FERNET_KEY'] = cls._fernet_env_backup
        super().tearDownClass()

    def _make_ticket(self):
        code = self.gen.generate_activation_code()
        license = self.env['wb.license.key'].create({
            'product_id': self.product.id,
            'partner_id': self.partner.id,
            'state': 'issued',
            'valid_from': fields.Date.today(),
            'valid_to': fields.Date.today() + timedelta(days=365),
            'activation_hash': self.gen.hash_activation_code(code),
        })
        ticket = self.env['wb.activation.ticket'].create({
            'license_id': license.id,
            'email': self.partner.email,
            'encrypted_code': self.gen.encrypt_code(code),
        })
        return ticket, code

    def test_ticket_token_format(self):
        ticket, _ = self._make_ticket()
        self.assertTrue(ticket.name.startswith('TCKT-'))
        self.assertEqual(len(ticket.name), 27)

    def test_register_landing_pins_ip(self):
        ticket, _ = self._make_ticket()
        ticket.register_landing(ip='1.2.3.4')
        self.assertEqual(ticket.bound_ip, '1.2.3.4')
        self.assertEqual(ticket.state, 'awaiting_otp')

    def test_register_landing_with_different_ip_counts_mismatch(self):
        ticket, _ = self._make_ticket()
        ticket.register_landing(ip='1.2.3.4')
        ticket.register_landing(ip='9.9.9.9')
        self.assertEqual(ticket.ip_mismatch_count, 1)
        self.assertEqual(ticket.bound_ip, '1.2.3.4', "bound_ip darf NICHT überschrieben werden.")

    def test_three_ip_mismatches_revoke_ticket(self):
        ticket, _ = self._make_ticket()
        ticket.register_landing(ip='1.2.3.4')
        ticket.register_landing(ip='9.9.9.9')
        ticket.register_landing(ip='9.9.9.9')
        with self.assertRaises(UserError):
            ticket.register_landing(ip='9.9.9.9')

    def test_send_otp_creates_hash_and_resets_attempts(self):
        ticket, _ = self._make_ticket()
        ticket.register_landing(ip='1.2.3.4')
        ticket.otp_attempts = 3
        otp = ticket.send_otp(ip='1.2.3.4')
        self.assertEqual(len(otp), 6)
        self.assertTrue(otp.isdigit())
        self.assertTrue(ticket.current_otp_hash)
        self.assertEqual(ticket.otp_attempts, 0)

    def test_send_otp_wrong_ip_fails(self):
        ticket, _ = self._make_ticket()
        ticket.register_landing(ip='1.2.3.4')
        with self.assertRaises(UserError):
            ticket.send_otp(ip='9.9.9.9')

    def test_verify_otp_success_returns_code(self):
        ticket, code = self._make_ticket()
        ticket.register_landing(ip='1.2.3.4')
        otp = ticket.send_otp(ip='1.2.3.4')
        revealed = ticket.verify_otp(otp, ip='1.2.3.4')
        self.assertEqual(revealed, code)
        self.assertEqual(ticket.state, 'code_revealed')
        self.assertFalse(ticket.current_otp_hash, "OTP-Hash muss nach Reveal entwertet werden.")

    def test_verify_otp_wrong_increments_attempts(self):
        ticket, _ = self._make_ticket()
        ticket.register_landing(ip='1.2.3.4')
        ticket.send_otp(ip='1.2.3.4')
        result = ticket.verify_otp('000000', ip='1.2.3.4')
        self.assertIsNone(result)
        self.assertEqual(ticket.otp_attempts, 1)

    def test_verify_otp_after_5_attempts_revokes(self):
        ticket, _ = self._make_ticket()
        ticket.register_landing(ip='1.2.3.4')
        ticket.send_otp(ip='1.2.3.4')
        ticket.otp_attempts = 5
        with self.assertRaises(UserError):
            ticket.verify_otp('000000', ip='1.2.3.4')

    def test_action_consume_clears_encrypted_code(self):
        ticket, code = self._make_ticket()
        self.assertTrue(ticket.encrypted_code)
        ticket.action_consume()
        self.assertEqual(ticket.state, 'consumed')
        self.assertFalse(ticket.encrypted_code, "encrypted_code muss nach Consume gelöscht werden.")

    def test_cron_cleanup_marks_old_tickets_expired(self):
        ticket, _ = self._make_ticket()
        ticket.expires_at = fields.Datetime.now() - timedelta(hours=1)
        self.env['wb.activation.ticket']._cron_cleanup_expired()
        ticket.invalidate_recordset()
        self.assertEqual(ticket.state, 'expired')
        self.assertFalse(ticket.encrypted_code)
