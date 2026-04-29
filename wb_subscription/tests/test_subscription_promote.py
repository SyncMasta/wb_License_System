"""Tests für action_confirm-Override + Lizenz-Issuance-Idempotenz.

Deckt ab:
- _wb_first_period_end: Kalender-Quartale/Halbjahre/Jahr/Monat korrekt berechnet
- _wb_promote_to_subscription: setzt is_subscription, plan_id, end_date, next_invoice_date
- RedirectWarning wenn Lizenz-Produkt keinen Default-Plan hat
- Idempotenz in _wb_issue_license_keys:
  * mid-cycle Payment auf active/issued Key → skip
  * Year-Rollover auf expired Key (aktiviert) → action_renew
  * Year-Rollover auf expired Key (nicht aktiviert) → action_renew (state bleibt expired)
  * revoked/cancelled Key → skip (kein Auto-Reactivate)
"""

import os
from datetime import date, timedelta
from unittest.mock import patch

from cryptography.fernet import Fernet
from odoo import fields
from odoo.exceptions import RedirectWarning
from odoo.tests import TransactionCase, tagged


@tagged('wb_subscription', 'wb_subscription_promote', '-at_install', 'post_install')
class TestSubscriptionPromote(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._fernet_env_backup = os.environ.get('WB_SUBSCRIPTION_FERNET_KEY')
        os.environ['WB_SUBSCRIPTION_FERNET_KEY'] = (
            Fernet.generate_key().decode('utf-8'))

        cls.gen = cls.env['wb.key.generator']
        cls.has_subscription = 'sale.subscription.plan' in cls.env

        cls.product_template = cls.env['product.template'].create({
            'name': 'TEST Lizenz-Sub',
            'wb_is_license_product': True,
            'wb_technical_code': 'TEST',
            'wb_billing_calendar': 'monthly',
            'list_price': 100.0,
            'type': 'service',
        })
        cls.product = cls.product_template.product_variant_id

        if cls.has_subscription:
            cls.plan = cls.env['sale.subscription.plan'].create({
                'name': 'Monthly TEST',
                'billing_period_value': 1,
                'billing_period_unit': 'month',
            })
            cls.product_template.wb_default_subscription_plan_id = cls.plan.id

        cls.partner = cls.env['res.partner'].create({
            'name': 'Test-Kunde GmbH',
            'email': 'kunde@example.com',
        })

    @classmethod
    def tearDownClass(cls):
        if cls._fernet_env_backup is None:
            os.environ.pop('WB_SUBSCRIPTION_FERNET_KEY', None)
        else:
            os.environ['WB_SUBSCRIPTION_FERNET_KEY'] = cls._fernet_env_backup
        super().tearDownClass()

    def _make_order(self):
        SO = self.env['sale.order'].sudo()
        return SO.create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id,
                'product_uom_qty': 1,
                'price_unit': 100.0,
            })],
        })

    # ---------------------------------------------------------- _wb_first_period_end

    def test_first_period_end_monthly(self):
        SO = self.env['sale.order']
        # Mitte Mai → Ende Mai
        self.assertEqual(
            SO._wb_first_period_end(date(2026, 5, 15), 'monthly'),
            date(2026, 5, 31),
        )
        # Februar (28 Tage) korrekt
        self.assertEqual(
            SO._wb_first_period_end(date(2026, 2, 10), 'monthly'),
            date(2026, 2, 28),
        )

    def test_first_period_end_quarterly_kalenderquartal(self):
        SO = self.env['sale.order']
        # 15.05. → Ende Q2 (Apr-Jun) = 30.06.
        self.assertEqual(
            SO._wb_first_period_end(date(2026, 5, 15), 'quarterly'),
            date(2026, 6, 30),
        )
        # 10.01. → Ende Q1 (Jan-Mär) = 31.03.
        self.assertEqual(
            SO._wb_first_period_end(date(2026, 1, 10), 'quarterly'),
            date(2026, 3, 31),
        )
        # 20.10. → Ende Q4 (Okt-Dez) = 31.12.
        self.assertEqual(
            SO._wb_first_period_end(date(2026, 10, 20), 'quarterly'),
            date(2026, 12, 31),
        )

    def test_first_period_end_biannual_kalenderhalbjahr(self):
        SO = self.env['sale.order']
        # 15.05. → Ende H1 (Jan-Jun) = 30.06.
        self.assertEqual(
            SO._wb_first_period_end(date(2026, 5, 15), 'biannual'),
            date(2026, 6, 30),
        )
        # 15.07. → Ende H2 (Jul-Dez) = 31.12.
        self.assertEqual(
            SO._wb_first_period_end(date(2026, 7, 15), 'biannual'),
            date(2026, 12, 31),
        )

    def test_first_period_end_yearly(self):
        SO = self.env['sale.order']
        self.assertEqual(
            SO._wb_first_period_end(date(2026, 5, 15), 'yearly'),
            date(2026, 12, 31),
        )

    # ---------------------------------------------------------- _wb_promote_to_subscription

    def test_promote_sets_is_subscription_and_plan(self):
        if not self.has_subscription:
            self.skipTest("sale_subscription nicht installiert")
        order = self._make_order()
        order.action_confirm()
        self.assertTrue(order.is_subscription)
        self.assertEqual(order.plan_id, self.plan)

    def test_promote_redirect_warning_when_no_plan(self):
        if not self.has_subscription:
            self.skipTest("sale_subscription nicht installiert")
        # Plan vom Produkt entfernen
        self.product_template.wb_default_subscription_plan_id = False
        order = self._make_order()
        with self.assertRaises(RedirectWarning) as cm:
            order.action_confirm()
        self.assertIn(self.product_template.name, str(cm.exception))
        # Wiederherstellen für Folge-Tests
        self.product_template.wb_default_subscription_plan_id = self.plan.id

    def test_promote_skips_non_license_orders(self):
        plain_product = self.env['product.template'].create({
            'name': 'Plain Service',
            'type': 'service',
            'list_price': 50.0,
        }).product_variant_id
        SO = self.env['sale.order'].sudo()
        order = SO.create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {
                'product_id': plain_product.id,
                'product_uom_qty': 1,
            })],
        })
        # Soll OHNE Exception durchlaufen — nicht-Lizenz-Order wird ignoriert
        order.action_confirm()
        self.assertFalse(order.wb_is_license_sub)

    # ---------------------------------------------------------- Idempotenz

    def _make_existing_key(self, state, valid_to=None, activated_at=None,
                          sale_order_id=None):
        code = self.gen.generate_activation_code()
        Key = self.env['wb.license.key'].sudo()
        key = Key.create({
            'product_id': self.product.id,
            'partner_id': self.partner.id,
            'sale_order_id': sale_order_id,
            'state': state,
            'valid_from': date(2026, 1, 1),
            'valid_to': valid_to or date(2026, 12, 31),
            'activation_hash': self.gen.hash_activation_code(code),
            'activation_expires_at': fields.Datetime.now() + timedelta(days=90),
        })
        if activated_at:
            key.write({
                'activated_at': activated_at,
                'activation_hash': False,
                'bound_domain': 'kunde.example.com',
                'bound_db_uuid': 'db-uuid-1234',
            })
        return key

    def test_idempotency_active_key_skips(self):
        order = self._make_order()
        existing = self._make_existing_key(
            state='active',
            activated_at=fields.Datetime.now(),
            sale_order_id=order.id,
        )
        before_valid_to = existing.valid_to
        order._wb_issue_license_keys()
        existing.invalidate_recordset()
        # Mid-cycle: Key bleibt unverändert
        self.assertEqual(existing.state, 'active')
        self.assertEqual(existing.valid_to, before_valid_to)
        self.assertFalse(existing.last_renewal_date)

    def test_idempotency_year_rollover_activated_renews(self):
        order = self._make_order()
        old_valid_to = date(2025, 12, 31)
        existing = self._make_existing_key(
            state='expired',
            valid_to=old_valid_to,
            activated_at=fields.Datetime.now() - timedelta(days=400),
            sale_order_id=order.id,
        )
        order._wb_issue_license_keys()
        existing.invalidate_recordset()
        # Key wurde verlängert, state wieder active (weil aktiviert)
        self.assertGreater(existing.valid_to, old_valid_to)
        self.assertEqual(existing.state, 'active')
        self.assertTrue(existing.last_renewal_date)

    def test_idempotency_year_rollover_unactivated_renews_but_stays_expired(self):
        order = self._make_order()
        old_valid_to = date(2025, 12, 31)
        existing = self._make_existing_key(
            state='expired',
            valid_to=old_valid_to,
            activated_at=False,  # nie aktiviert
            sale_order_id=order.id,
        )
        order._wb_issue_license_keys()
        existing.invalidate_recordset()
        # action_renew schreibt valid_to (egal), state bleibt expired
        self.assertGreater(existing.valid_to, old_valid_to)
        self.assertEqual(existing.state, 'expired')

    def test_idempotency_revoked_key_no_reactivate(self):
        order = self._make_order()
        existing = self._make_existing_key(
            state='revoked',
            activated_at=fields.Datetime.now(),
            sale_order_id=order.id,
        )
        # Vor _wb_issue_license_keys: nur 1 Key
        Key = self.env['wb.license.key'].sudo()
        before_count = Key.search_count([('sale_order_id', '=', order.id)])
        order._wb_issue_license_keys()
        existing.invalidate_recordset()
        # Key bleibt revoked, kein neuer Key erzeugt
        self.assertEqual(existing.state, 'revoked')
        self.assertEqual(
            Key.search_count([('sale_order_id', '=', order.id)]),
            before_count,
        )
