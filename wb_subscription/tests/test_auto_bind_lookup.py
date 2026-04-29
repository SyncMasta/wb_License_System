"""Tests fuer Zero-Touch-Aktivierung via /api/license/lookup.

Deckt ab:
- arm/disarm-Workflow (Pflichtfelder, State-Guards)
- Match-Logik: Domain-Match, Email-Match, Domain-Normalisierung
- Spoofing-Resistenz: kein armed-Flag → kein Match, abgelaufenes Fenster
  → kein Match, falsche Domain → kein Match, schon gebundene db_uuid → kein
  Match (ausser idempotent dieselbe), revoked-State → kein Match
- _bind_via_auto_lookup setzt aktiviert + disarmed
- _cron_disarm_auto_bind raeumt abgelaufene Armierungen
"""
import os
from datetime import timedelta
from unittest.mock import patch

from cryptography.fernet import Fernet
from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('wb_subscription', 'wb_auto_bind')
class TestAutoBindLookup(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._fernet_env_backup = os.environ.get('WB_SUBSCRIPTION_FERNET_KEY')
        os.environ['WB_SUBSCRIPTION_FERNET_KEY'] = (
            Fernet.generate_key().decode('utf-8'))

        cls.gen = cls.env['wb.key.generator']
        cls.product = cls.env['product.template'].create({
            'name': 'TEST Auto-Bind Produkt',
            'wb_is_license_product': True,
            'wb_technical_code': 'AUTO',
            'wb_instance_limit': 1,
        }).product_variant_id
        cls.partner = cls.env['res.partner'].create({
            'name': 'Auto-Bind Kunde',
            'email': 'auto@example.com',
        })

    @classmethod
    def tearDownClass(cls):
        if cls._fernet_env_backup is None:
            os.environ.pop('WB_SUBSCRIPTION_FERNET_KEY', None)
        else:
            os.environ['WB_SUBSCRIPTION_FERNET_KEY'] = cls._fernet_env_backup
        super().tearDownClass()

    def _make_license(self, **overrides):
        code = self.gen.generate_activation_code()
        vals = {
            'product_id': self.product.id,
            'partner_id': self.partner.id,
            'state': 'issued',
            'valid_from': fields.Date.today(),
            'valid_to': fields.Date.today() + timedelta(days=365),
            'activation_hash': self.gen.hash_activation_code(code),
            'activation_expires_at': fields.Datetime.now() + timedelta(days=90),
        }
        vals.update(overrides)
        return self.env['wb.license.key'].create(vals)

    # ------------------------------------------------------------- arm/disarm

    def test_arm_requires_domain_or_email(self):
        license = self._make_license()
        with self.assertRaises(UserError):
            license.action_arm_auto_bind()

    def test_arm_with_domain_succeeds(self):
        license = self._make_license(pre_assigned_domain='kunde.odoo.com')
        license.action_arm_auto_bind()
        self.assertTrue(license.auto_bind_armed)
        self.assertTrue(license.auto_bind_armed_until)
        self.assertTrue(license.auto_bind_armed_at)
        self.assertEqual(license.auto_bind_armed_by, self.env.user)

    def test_arm_revoked_blocked(self):
        license = self._make_license(
            pre_assigned_domain='kunde.odoo.com',
            state='revoked',
            activated_at=fields.Datetime.now(),
        )
        with self.assertRaises(UserError):
            license.action_arm_auto_bind()

    def test_arm_already_bound_blocked(self):
        license = self._make_license(
            pre_assigned_domain='kunde.odoo.com',
            bound_db_uuid='db-1',
            bound_domain='kunde.odoo.com',
            activated_at=fields.Datetime.now(),
            state='active',
        )
        with self.assertRaises(UserError):
            license.action_arm_auto_bind()

    def test_disarm_clears_flags(self):
        license = self._make_license(pre_assigned_domain='kunde.odoo.com')
        license.action_arm_auto_bind()
        license.action_disarm_auto_bind()
        self.assertFalse(license.auto_bind_armed)
        self.assertFalse(license.auto_bind_armed_until)

    # ------------------------------------------------------------ Match-Logik

    def test_lookup_match_by_domain(self):
        license = self._make_license(pre_assigned_domain='kunde.odoo.com')
        license.action_arm_auto_bind()
        match = self.env['wb.license.key']._lookup_for_auto_bind(
            'AUTO', 'db-x', 'https://kunde.odoo.com/', '')
        self.assertEqual(match, license)

    def test_lookup_match_by_email_when_domain_unknown(self):
        license = self._make_license(pre_assigned_email='Auto@Example.com')
        license.action_arm_auto_bind()
        match = self.env['wb.license.key']._lookup_for_auto_bind(
            'AUTO', 'db-x', '', 'auto@example.com')
        self.assertEqual(match, license)

    def test_lookup_no_match_when_not_armed(self):
        self._make_license(pre_assigned_domain='kunde.odoo.com')
        match = self.env['wb.license.key']._lookup_for_auto_bind(
            'AUTO', 'db-x', 'kunde.odoo.com', '')
        self.assertFalse(match)

    def test_lookup_no_match_when_window_expired(self):
        license = self._make_license(pre_assigned_domain='kunde.odoo.com')
        license.action_arm_auto_bind()
        license.auto_bind_armed_until = fields.Datetime.now() - timedelta(hours=1)
        match = self.env['wb.license.key']._lookup_for_auto_bind(
            'AUTO', 'db-x', 'kunde.odoo.com', '')
        self.assertFalse(match)

    def test_lookup_no_match_wrong_domain(self):
        license = self._make_license(pre_assigned_domain='kunde.odoo.com')
        license.action_arm_auto_bind()
        match = self.env['wb.license.key']._lookup_for_auto_bind(
            'AUTO', 'db-x', 'attacker.de', '')
        self.assertFalse(match)

    def test_lookup_no_match_wrong_product_code(self):
        license = self._make_license(pre_assigned_domain='kunde.odoo.com')
        license.action_arm_auto_bind()
        match = self.env['wb.license.key']._lookup_for_auto_bind(
            'BITW', 'db-x', 'kunde.odoo.com', '')
        self.assertFalse(match)

    def test_lookup_no_match_when_revoked(self):
        license = self._make_license(
            pre_assigned_domain='kunde.odoo.com',
            activated_at=fields.Datetime.now(),
        )
        license.action_arm_auto_bind()
        license.write({'auto_bind_armed': True, 'state': 'revoked'})
        match = self.env['wb.license.key']._lookup_for_auto_bind(
            'AUTO', 'db-x', 'kunde.odoo.com', '')
        self.assertFalse(match)

    def test_lookup_idempotent_for_same_db_uuid(self):
        license = self._make_license(pre_assigned_domain='kunde.odoo.com')
        license.action_arm_auto_bind()
        license._bind_via_auto_lookup('db-x', 'kunde.odoo.com', '')
        # Re-arm und neuer Lookup mit derselben db_uuid wird wieder matchen
        # (z.B. wenn DB neu installiert auf gleicher UUID — selten).
        license.write({
            'auto_bind_armed': True,
            'auto_bind_armed_until': fields.Datetime.now() + timedelta(hours=1),
        })
        match = self.env['wb.license.key']._lookup_for_auto_bind(
            'AUTO', 'db-x', 'kunde.odoo.com', '')
        self.assertEqual(match, license)

    def test_lookup_no_match_for_different_db_uuid_when_already_bound(self):
        license = self._make_license(pre_assigned_domain='kunde.odoo.com')
        license.action_arm_auto_bind()
        license._bind_via_auto_lookup('db-x', 'kunde.odoo.com', '')
        license.write({
            'auto_bind_armed': True,
            'auto_bind_armed_until': fields.Datetime.now() + timedelta(hours=1),
        })
        match = self.env['wb.license.key']._lookup_for_auto_bind(
            'AUTO', 'OTHER-DB', 'kunde.odoo.com', '')
        self.assertFalse(match,
            "Lizenz darf nicht an eine andere db_uuid umgebunden werden.")

    # -------------------------------------------------------------- Bind-Path

    def test_bind_via_auto_lookup_activates_license(self):
        license = self._make_license(pre_assigned_domain='kunde.odoo.com')
        license.action_arm_auto_bind()
        license._bind_via_auto_lookup(
            'db-zzz', 'https://kunde.odoo.com/', 'kunde@example.com')
        license.invalidate_recordset()
        self.assertEqual(license.state, 'active')
        self.assertEqual(license.bound_db_uuid, 'db-zzz')
        self.assertTrue(license.activated_at)
        self.assertFalse(license.auto_bind_armed)
        self.assertFalse(license.activation_hash,
            "Activation-Hash muss nach Auto-Bind entwertet sein.")
        events = self.env['wb.license.event'].search([
            ('license_id', '=', license.id),
            ('event_type', '=', 'auto_bind_resolved'),
        ])
        self.assertTrue(events)

    # ---------------------------------------------------------------- Cron

    def test_cron_disarms_expired_only(self):
        active = self._make_license(pre_assigned_domain='a.de')
        expired = self._make_license(pre_assigned_domain='b.de')
        active.action_arm_auto_bind()
        expired.action_arm_auto_bind()
        expired.auto_bind_armed_until = fields.Datetime.now() - timedelta(minutes=1)

        count = self.env['wb.license.key']._cron_disarm_auto_bind()
        self.assertEqual(count, 1)
        active.invalidate_recordset()
        expired.invalidate_recordset()
        self.assertTrue(active.auto_bind_armed)
        self.assertFalse(expired.auto_bind_armed)

    # --------------------------------------------------------- Domain-Normalize

    def test_domain_normalization(self):
        Key = self.env['wb.license.key']
        self.assertEqual(Key._normalize_domain('https://X.de/'), 'x.de')
        self.assertEqual(Key._normalize_domain('http://x.de'), 'x.de')
        self.assertEqual(Key._normalize_domain('  X.DE  '), 'x.de')
        self.assertEqual(Key._normalize_domain(''), '')
        self.assertEqual(Key._normalize_domain(False), '')
