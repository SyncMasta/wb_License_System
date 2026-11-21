# -*- coding: utf-8 -*-
"""DEV-Lizenz: Verkauf per Sale-Order, Upgrade auf Produktiv, Umzug.

DECISIONS.md #91-93.
"""

import json
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import HttpCase, TransactionCase, tagged

from .test_instance_resolution import PROD_DOMAIN, _InstanceSetup

DEV_DOMAIN = 'https://staging.kunde.example.com'
DEV_UUID = 'db-staging-uuid'


class _DevSetup(_InstanceSetup):

    @classmethod
    def _setup_dev(cls):
        cls._setup_license()
        cls.prod_tmpl = cls.license.product_id.product_tmpl_id
        cls.prod_tmpl.write({'list_price': 100.0, 'type': 'service'})
        cls.dev_tmpl = cls.env['product.template'].create({
            'name': 'INST DEV-Lizenz',
            'wb_is_license_product': True,
            'wb_dev_of_tmpl_id': cls.prod_tmpl.id,
            'list_price': 30.0,
            'type': 'service',
        })
        cls.has_subscription = 'sale.subscription.plan' in cls.env
        if cls.has_subscription:
            plan = cls.env['sale.subscription.plan'].create({
                'name': 'Monthly INST',
                'billing_period_value': 1,
                'billing_period_unit': 'month',
            })
            for tmpl in (cls.prod_tmpl, cls.dev_tmpl):
                tmpl.write({
                    'recurring_invoice': True,
                    'wb_default_subscription_plan_id': plan.id,
                })

    def _staging_instance(self, neutralized=True):
        self.license.resolve_instance(DEV_DOMAIN, DEV_UUID, neutralized)
        return self.env['wb.license.instance'].search([
            ('license_id', '=', self.license.id), ('db_uuid', '=', DEV_UUID)])

    def _paid_dev_license(self):
        """Angebot an der Instanz, Zahlung simuliert ueber den Issuance-Hook."""
        inst = self._staging_instance()
        action = inst.action_create_dev_quotation()
        order = self.env['sale.order'].browse(action['res_id'])
        order._wb_issue_license_keys()
        return inst, order, inst.dev_license_id


@tagged('wb_subscription', 'wb_dev_license', 'post_install', '-at_install')
class TestDevLicenseSales(_DevSetup, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_dev()

    @classmethod
    def tearDownClass(cls):
        cls._restore_env()
        super().tearDownClass()

    def test_dev_product_has_no_own_code(self):
        with self.assertRaises(ValidationError):
            self.dev_tmpl.wb_technical_code = 'INSD'

    def test_quotation_links_instance_and_issues_on_payment(self):
        inst = self._staging_instance()
        action = inst.action_create_dev_quotation()
        order = self.env['sale.order'].browse(action['res_id'])
        self.assertEqual(order.partner_id, self.license.partner_id)
        self.assertEqual(order.order_line.product_id, self.dev_tmpl.product_variant_id)
        self.assertEqual(order.order_line.wb_license_instance_id, inst)
        self.assertFalse(inst.dev_license_id, "Key erst nach Zahlung")

        order._wb_issue_license_keys()
        dev = inst.dev_license_id
        self.assertEqual(dev.parent_license_id, self.license)
        self.assertEqual(dev.product_id, self.license.product_id)
        self.assertEqual(dev.sale_order_id, order)
        self.assertEqual(dev.bound_db_uuid, DEV_UUID)
        self.assertEqual(dev.state, 'active')
        self.assertEqual(dev.valid_to, order._wb_compute_valid_to())

        # Zweite Zahlung (Mid-Cycle) erzeugt keinen zweiten Key.
        order._wb_issue_license_keys()
        self.assertEqual(
            self.env['wb.license.key'].search_count([('parent_license_id', '=', self.license.id)]), 1)

    def test_dev_license_validity(self):
        _inst, _order, dev = self._paid_dev_license()
        res = self.license.resolve_instance(DEV_DOMAIN, DEV_UUID, True)
        self.assertEqual(res, {'status': 'dev', 'until': dev.valid_to})
        res = self.license.resolve_instance(DEV_DOMAIN, DEV_UUID, False)
        self.assertEqual(res['status'], 'dev_not_neutralized')
        # Mit dem eigenen Key gelten dieselben Regeln.
        self.assertIsNone(dev.resolve_instance(DEV_DOMAIN, DEV_UUID, True))
        self.assertEqual(
            dev.resolve_instance(DEV_DOMAIN, DEV_UUID, False)['status'], 'dev_not_neutralized')
        self.assertEqual(
            dev.resolve_instance(PROD_DOMAIN, 'db-prod-uuid', True)['status'], 'mismatch')

    def test_quotation_rules(self):
        inst = self._staging_instance(neutralized=False)
        with self.assertRaises(UserError):
            inst.action_create_dev_quotation()
        inst, _order, _dev = self._paid_dev_license()
        with self.assertRaises(UserError):
            inst.action_create_dev_quotation()

    def test_quotation_needs_exactly_one_dev_product(self):
        self.dev_tmpl.copy({'name': 'Zweites DEV-Produkt'})
        with self.assertRaises(UserError):
            self._staging_instance().action_create_dev_quotation()

    def test_convert_to_production(self):
        inst, order, dev = self._paid_dev_license()
        prod = dev._convert_to_production(order)
        self.assertEqual(prod.state, 'active')
        self.assertFalse(prod.parent_license_id)
        self.assertEqual(prod.product_id, self.license.product_id)
        self.assertEqual(prod.bound_db_uuid, DEV_UUID)
        self.assertEqual(prod.valid_to, dev.valid_to)
        self.assertEqual(dev.state, 'cancelled')
        self.assertEqual(dev.converted_to_license_id, prod)
        self.assertTrue(dev.converted_at)
        # Idempotent.
        self.assertEqual(dev._convert_to_production(order), prod)

        # Die DB meldet sich weiter mit dem alten Produktiv-Key, jetzt live.
        res = self.license.resolve_instance(DEV_DOMAIN, DEV_UUID, False)
        self.assertEqual(res['status'], 'production')
        self.assertEqual(res['state'], 'active')
        self.assertEqual(res['until'], prod.valid_to)
        self.assertEqual(inst.status, 'production')
        # Mit dem neuen Key ist es die gebundene Instanz.
        self.assertIsNone(prod.resolve_instance(DEV_DOMAIN, DEV_UUID, False))

    def test_upsell_without_upgrade_creates_no_keys(self):
        _inst, order, _dev = self._paid_dev_license()
        upsell = self.env['sale.order'].create({
            'partner_id': order.partner_id.id,
            'order_line': [(0, 0, {'product_id': self.prod_tmpl.product_variant_id.id})],
        })
        if 'subscription_state' not in upsell._fields:
            self.skipTest("sale_subscription nicht installiert")
        upsell.subscription_state = '7_upsell'
        before = self.env['wb.license.key'].search_count([])
        upsell._wb_issue_license_keys()
        self.assertEqual(self.env['wb.license.key'].search_count([]), before)

    def test_upgrade_via_subscription_upsell(self):
        if not self.has_subscription:
            self.skipTest("sale_subscription nicht installiert")
        _inst, order, dev = self._paid_dev_license()
        order.action_confirm()
        self.assertTrue(order.is_subscription)
        # Abo gilt als bereits abgerechnet: naechste Rechnung liegt in der Zukunft.
        today = fields.Date.today()
        order.write({'start_date': today - timedelta(days=10),
                     'next_invoice_date': today + timedelta(days=20)})

        action = dev.action_upgrade_to_production()
        upsell = self.env['sale.order'].browse(action['res_id'])
        self.assertEqual(upsell.subscription_state, '7_upsell')
        self.assertEqual(upsell.subscription_id, order)
        self.assertEqual(upsell.wb_upgrade_license_id, dev)
        lines = upsell.order_line.filtered(lambda l: not l.display_type)
        qty = {l.product_id: l.product_uom_qty for l in lines}
        self.assertEqual(qty[self.dev_tmpl.product_variant_id], -1)
        self.assertEqual(qty[self.prod_tmpl.product_variant_id], 1)
        # Odoo rechnet beide Zeilen anteilig: der Kunde zahlt nur die
        # Differenz (100 - 30) fuer die Restperiode.
        discounts = set(lines.mapped('discount'))
        self.assertEqual(len(discounts), 1, lines.mapped(lambda l: (l.name, l.discount)))
        ratio = 1 - discounts.pop() / 100
        self.assertLess(ratio, 1)
        self.assertAlmostEqual(upsell.amount_untaxed, 70.0 * ratio, places=2)

        upsell.action_confirm()
        self.assertFalse(upsell.is_subscription, "Upsell wird kein eigenes Abo")
        sub_qty = {}
        for line in order.order_line:
            sub_qty[line.product_id] = sub_qty.get(line.product_id, 0) + line.product_uom_qty
        self.assertEqual(sub_qty[self.dev_tmpl.product_variant_id], 0)
        self.assertEqual(sub_qty[self.prod_tmpl.product_variant_id], 1)

        upsell._wb_issue_license_keys()
        prod = dev.converted_to_license_id
        self.assertTrue(prod)
        self.assertEqual(prod.sale_order_id, order)
        self.assertEqual(prod.valid_to, dev.valid_to)

        with self.assertRaises(UserError):
            dev.action_upgrade_to_production()

    def test_migration_releases_and_next_copy_claims(self):
        _inst, _order, dev = self._paid_dev_license()
        migration = self.env['wb.license.migration.request'].create({
            'license_id': self.license.id,
            'new_domain': DEV_DOMAIN,
            'new_db_uuid': DEV_UUID,
            'reason': 'Upgrade auf der Staging-DB, die jetzt live geht.',
            'contact_email': 'kunde@example.com',
        })
        migration.action_approve()
        self.assertEqual(self.license.bound_db_uuid, DEV_UUID)
        self.assertFalse(dev.bound_db_uuid)
        self.assertEqual(dev.state, 'active')
        # Frei heisst: gilt nirgends.
        self.assertEqual(
            dev.resolve_instance('https://x.example.com', 'db-x', True)['status'], 'mismatch')
        # Die neue Produktiv-DB ist jetzt die gebundene Instanz.
        self.assertIsNone(self.license.resolve_instance(DEV_DOMAIN, DEV_UUID, False))

        # Naechste neutralisierte Kopie uebernimmt die DEV-Lizenz, keine Eval.
        res = self.license.resolve_instance('https://staging2.example.com', 'db-staging-2', True)
        self.assertEqual(res['status'], 'dev')
        self.assertEqual(dev.bound_db_uuid, 'db-staging-2')
        new_inst = self.env['wb.license.instance'].search([('db_uuid', '=', 'db-staging-2')])
        self.assertEqual(new_inst.dev_license_id, dev)
        self.assertFalse(new_inst.eval_until)
        # Eine weitere Kopie bekommt wieder nur die Eval.
        res = self.license.resolve_instance('https://staging3.example.com', 'db-staging-3', True)
        self.assertEqual(res['status'], 'eval')

    def test_invoice_with_license_product_triggers_hook(self):
        move = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.license.partner_id.id,
            'invoice_line_ids': [(0, 0, {
                'product_id': self.dev_tmpl.product_variant_id.id,
                'quantity': 1, 'price_unit': 30.0,
            })],
        })
        self.assertTrue(move.wb_has_license_lines)


@tagged('wb_subscription', 'wb_dev_license', 'post_install', '-at_install')
class TestCheckEndpointConverted(_DevSetup, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_dev()

    @classmethod
    def tearDownClass(cls):
        cls._restore_env()
        super().tearDownClass()

    def test_converted_db_answers_like_production(self):
        _inst, order, dev = self._paid_dev_license()
        prod = dev._convert_to_production(order)
        resp = self.url_open(
            '/api/license/check',
            data=json.dumps({'jsonrpc': '2.0', 'method': 'call', 'params': {
                'key': self.license.name, 'domain': DEV_DOMAIN, 'db_uuid': DEV_UUID,
            }}),
            headers={'Content-Type': 'application/json'},
        )
        res = resp.json()['result']
        self.assertEqual(res['state'], 'active')
        self.assertEqual(res['valid_to'], prod.valid_to.isoformat())
        self.assertNotIn('instance', res)
