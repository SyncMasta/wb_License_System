# -*- coding: utf-8 -*-
"""Tests für Activate-Consents (EULA, AGB, DSGVO, Refund-Waiver, Newsletter).

Deckt:
* activate_with_code persistiert alle vier Consent-Timestamps
* activate_with_code loggt einen Audit-Event je Consent
* refund_waiver_confirmed_at wird gesetzt + Event 'refund_waiver_confirmed'
* Newsletter-Opt-In ohne mass_mailing → Event 'newsletter_optin_failed'
* Newsletter-Opt-In mit mass_mailing → Contact wird zur [PRODUCT_CODE]-Liste
  hinzugefügt
* Newsletter-Opt-In ohne passende Liste → Event 'newsletter_optin_failed'
"""
import os
from datetime import timedelta

from cryptography.fernet import Fernet
from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('wb_subscription', 'wb_activate_consents')
class TestActivationConsents(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._fernet_env_backup = os.environ.get('WB_SUBSCRIPTION_FERNET_KEY')
        os.environ['WB_SUBSCRIPTION_FERNET_KEY'] = (
            Fernet.generate_key().decode('utf-8'))
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
            'email': 'admin@kunde.example.com',
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

    def _consents(self, **overrides):
        base = {
            'eula': True,
            'terms': True,
            'privacy': True,
            'refund_waiver': True,
            'newsletter': False,
            'email': 'admin@kunde.example.com',
        }
        base.update(overrides)
        return base

    def test_activate_persists_all_consent_timestamps(self):
        license, code = self._make_issued_license()
        result = license.activate_with_code(
            code, 'kunde.example.com', 'db-uuid-1',
            ip='1.2.3.4', user_agent='test-ua',
            consents=self._consents(),
        )
        self.assertEqual(result['status'], 'ok')
        license.invalidate_recordset()
        self.assertTrue(license.eula_accepted_at)
        self.assertTrue(license.terms_accepted_at)
        self.assertTrue(license.privacy_accepted_at)
        self.assertTrue(license.refund_waiver_confirmed_at)
        self.assertEqual(license.activation_consent_email, 'admin@kunde.example.com')
        self.assertEqual(license.activation_consent_ip, '1.2.3.4')

    def test_activate_logs_one_event_per_consent(self):
        license, code = self._make_issued_license()
        license.activate_with_code(
            code, 'kunde.example.com', 'db-uuid-2',
            consents=self._consents(),
        )
        events = self.env['wb.license.event'].search([
            ('license_id', '=', license.id),
        ])
        types = events.mapped('event_type')
        self.assertIn('eula_accepted', types)
        self.assertIn('terms_accepted', types)
        self.assertIn('privacy_accepted', types)
        self.assertIn('refund_waiver_confirmed', types)
        self.assertIn('activation', types)

    def test_activate_without_consents_skips_persistence(self):
        # Backwards-Compat: alter Pfad ohne consents=...
        license, code = self._make_issued_license()
        result = license.activate_with_code(
            code, 'kunde.example.com', 'db-uuid-3',
        )
        self.assertEqual(result['status'], 'ok')
        license.invalidate_recordset()
        self.assertFalse(license.eula_accepted_at)
        self.assertFalse(license.refund_waiver_confirmed_at)

    def test_newsletter_optin_without_mailing_module_logs_failure(self):
        if 'mailing.list' in self.env:
            self.skipTest("mass_mailing installiert — Soft-Dep-Pfad nicht prüfbar")
        license, code = self._make_issued_license()
        license.activate_with_code(
            code, 'kunde.example.com', 'db-uuid-4',
            consents=self._consents(newsletter=True),
        )
        events = self.env['wb.license.event'].search([
            ('license_id', '=', license.id),
            ('event_type', '=', 'newsletter_optin_failed'),
        ])
        self.assertTrue(events)

    def test_newsletter_optin_adds_contact_to_product_list(self):
        if 'mailing.list' not in self.env:
            self.skipTest("mass_mailing nicht installiert")
        MailingList = self.env['mailing.list']
        # Liste mit Prefix [TEST] anlegen
        mlist = MailingList.create({
            'name': '[TEST] Test-Modul Updates',
            'is_public': False,
        })
        license, code = self._make_issued_license()
        license.activate_with_code(
            code, 'kunde.example.com', 'db-uuid-5',
            consents=self._consents(newsletter=True, email='news@kunde.de'),
        )
        contact = self.env['mailing.contact'].search(
            [('email', '=ilike', 'news@kunde.de')], limit=1)
        self.assertTrue(contact)
        self.assertIn(mlist.id, contact.list_ids.ids)
        license.invalidate_recordset()
        self.assertTrue(license.newsletter_optin_at)
        self.assertEqual(license.newsletter_optin_email, 'news@kunde.de')

    def test_newsletter_optin_without_matching_list_logs_failure(self):
        if 'mailing.list' not in self.env:
            self.skipTest("mass_mailing nicht installiert")
        # Bewusst KEINE Liste mit Prefix [TEST] anlegen —
        # vorhandene Listen aus Demo-Daten dürfen den Test nicht stören.
        existing = self.env['mailing.list'].search(
            [('name', '=like', '[TEST]%')])
        existing.unlink()
        license, code = self._make_issued_license()
        license.activate_with_code(
            code, 'kunde.example.com', 'db-uuid-6',
            consents=self._consents(newsletter=True, email='news2@kunde.de'),
        )
        events = self.env['wb.license.event'].search([
            ('license_id', '=', license.id),
            ('event_type', '=', 'newsletter_optin_failed'),
        ])
        self.assertTrue(events)
        license.invalidate_recordset()
        # newsletter_optin_at trotzdem gesetzt (Consent dokumentiert),
        # aber kein Contact angelegt
        self.assertTrue(license.newsletter_optin_at)
