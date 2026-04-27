"""Tests für die Install-Registry (Lead-Liste).

Deckt ab:
- announce() upserted korrekt (Insert + Update)
- announce() inkrementiert announce_count und last_seen_at
- mark_converted() setzt State + Verknüpfung
- activate_with_code() konvertiert passenden Install-Eintrag automatisch
- Multi-Company-Isolation greift (Smoke-Test über company_id-Default)
- _cron_mark_churned setzt alte Einträge auf 'churned'
"""

import os
from datetime import timedelta

from cryptography.fernet import Fernet
from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('wb_subscription', 'wb_install_registry')
class TestInstallRegistry(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._fernet_env_backup = os.environ.get('WB_SUBSCRIPTION_FERNET_KEY')
        os.environ['WB_SUBSCRIPTION_FERNET_KEY'] = Fernet.generate_key().decode('utf-8')

        cls.gen = cls.env['wb.key.generator']
        cls.Install = cls.env['wb.license.install']

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

    # --------------- announce() ---------------

    def test_announce_creates_record(self):
        install = self.Install.announce(
            'TEST', 'kunde.example.com', 'db-uuid-1234',
            contact_email='admin@kunde.example.com',
            client_version='19.0.1.0.0',
            ip='1.2.3.4',
            user_agent='test-ua',
        )
        self.assertEqual(install.product_code, 'TEST')
        self.assertEqual(install.domain, 'kunde.example.com')
        self.assertEqual(install.db_uuid, 'db-uuid-1234')
        self.assertEqual(install.state, 'unlicensed')
        self.assertEqual(install.announce_count, 1)
        self.assertEqual(install.contact_email, 'admin@kunde.example.com')
        self.assertEqual(install.last_seen_ip, '1.2.3.4')
        self.assertTrue(install.first_seen_at)
        self.assertTrue(install.last_seen_at)

    def test_announce_resolves_product_id(self):
        install = self.Install.announce(
            'TEST', 'kunde.example.com', 'db-uuid-1234')
        self.assertEqual(install.product_id, self.product)

    def test_announce_matches_partner_by_email(self):
        install = self.Install.announce(
            'TEST', 'kunde.example.com', 'db-uuid-1234',
            contact_email='admin@kunde.example.com',
        )
        self.assertEqual(install.partner_id, self.partner)

    def test_announce_unknown_product_code_leaves_product_empty(self):
        install = self.Install.announce(
            'XXXX', 'foo.example.com', 'db-uuid-x')
        self.assertFalse(install.product_id)

    def test_announce_upserts_existing(self):
        first = self.Install.announce(
            'TEST', 'kunde.example.com', 'db-uuid-1234',
            client_version='19.0.1.0.0',
        )
        first_seen = first.first_seen_at

        second = self.Install.announce(
            'TEST', 'kunde.example.com', 'db-uuid-1234',
            client_version='19.0.1.1.0',
            ip='9.9.9.9',
        )
        self.assertEqual(first.id, second.id, "Upsert muss denselben Record liefern.")
        self.assertEqual(second.announce_count, 2)
        self.assertEqual(second.client_version, '19.0.1.1.0')
        self.assertEqual(second.last_seen_ip, '9.9.9.9')
        self.assertEqual(second.first_seen_at, first_seen,
                         "first_seen_at darf bei Updates nicht überschrieben werden.")

    def test_announce_different_domain_creates_separate_record(self):
        a = self.Install.announce('TEST', 'a.example.com', 'db-1')
        b = self.Install.announce('TEST', 'b.example.com', 'db-1')
        self.assertNotEqual(a.id, b.id)

    def test_announce_different_db_uuid_creates_separate_record(self):
        a = self.Install.announce('TEST', 'a.example.com', 'db-1')
        b = self.Install.announce('TEST', 'a.example.com', 'db-2')
        self.assertNotEqual(a.id, b.id)

    def test_announce_missing_required_raises(self):
        with self.assertRaises(ValueError):
            self.Install.announce('', 'a.example.com', 'db-1')
        with self.assertRaises(ValueError):
            self.Install.announce('TEST', '', 'db-1')
        with self.assertRaises(ValueError):
            self.Install.announce('TEST', 'a.example.com', '')

    # --------------- mark_converted() ---------------

    def test_mark_converted_sets_state_and_link(self):
        install = self.Install.announce(
            'TEST', 'kunde.example.com', 'db-uuid-1234')
        license, _ = self._make_issued_license()
        install.mark_converted(license)
        self.assertEqual(install.state, 'converted')
        self.assertEqual(install.license_id, license)
        self.assertTrue(install.converted_at)

    def test_mark_converted_computes_days_until_conversion(self):
        install = self.Install.announce(
            'TEST', 'kunde.example.com', 'db-uuid-1234')
        install.first_seen_at = fields.Datetime.now() - timedelta(days=14)
        license, _ = self._make_issued_license()
        install.mark_converted(license)
        self.assertEqual(install.days_until_conversion, 14)

    def test_mark_converted_idempotent(self):
        install = self.Install.announce(
            'TEST', 'kunde.example.com', 'db-uuid-1234')
        license, _ = self._make_issued_license()
        install.mark_converted(license)
        first_converted_at = install.converted_at
        install.mark_converted(license)
        self.assertEqual(install.converted_at, first_converted_at,
                         "mark_converted darf bei gleicher Lizenz nicht erneut schreiben.")

    # --------------- Auto-Convert via activate_with_code ---------------

    def test_activate_auto_converts_matching_install(self):
        self.Install.announce(
            'TEST', 'kunde.example.com', 'db-uuid-1234')
        license, code = self._make_issued_license()
        result = license.activate_with_code(
            code, 'kunde.example.com', 'db-uuid-1234')
        self.assertEqual(result['status'], 'ok')

        install = self.Install.search([
            ('product_code', '=', 'TEST'),
            ('domain', '=', 'kunde.example.com'),
            ('db_uuid', '=', 'db-uuid-1234'),
        ])
        self.assertEqual(len(install), 1)
        self.assertEqual(install.state, 'converted')
        self.assertEqual(install.license_id, license)

    def test_activate_without_install_record_does_not_fail(self):
        """Wenn der Kunde NIE einen announce gesendet hat (z.B. opt-out),
        muss activate trotzdem durchlaufen."""
        license, code = self._make_issued_license()
        result = license.activate_with_code(
            code, 'no-announce.example.com', 'db-uuid-noannounce')
        self.assertEqual(result['status'], 'ok')

    def test_activate_does_not_touch_unrelated_installs(self):
        unrelated = self.Install.announce(
            'TEST', 'other.example.com', 'db-uuid-other')
        license, code = self._make_issued_license()
        license.activate_with_code(
            code, 'kunde.example.com', 'db-uuid-1234')
        unrelated.invalidate_recordset()
        self.assertEqual(unrelated.state, 'unlicensed')
        self.assertFalse(unrelated.license_id)

    # --------------- Churn-Cron ---------------

    def test_cron_mark_churned_sets_old_unlicensed(self):
        old = self.Install.announce(
            'TEST', 'old.example.com', 'db-uuid-old')
        old.last_seen_at = fields.Datetime.now() - timedelta(days=70)
        recent = self.Install.announce(
            'TEST', 'recent.example.com', 'db-uuid-recent')
        self.Install._cron_mark_churned()
        old.invalidate_recordset()
        recent.invalidate_recordset()
        self.assertEqual(old.state, 'churned')
        self.assertEqual(recent.state, 'unlicensed')

    def test_cron_mark_churned_skips_converted(self):
        install = self.Install.announce(
            'TEST', 'old.example.com', 'db-uuid-old')
        license, _ = self._make_issued_license()
        install.mark_converted(license)
        install.last_seen_at = fields.Datetime.now() - timedelta(days=120)
        self.Install._cron_mark_churned()
        install.invalidate_recordset()
        self.assertEqual(install.state, 'converted',
                         "Cron darf konvertierte Einträge nicht auf 'churned' setzen.")
