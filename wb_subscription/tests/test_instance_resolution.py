# -*- coding: utf-8 -*-
"""Tests fuer Test-DB-Eval, DEV-Lizenz und fremde Instanzen (DECISIONS.md #80-82)."""

import json
import os
from datetime import timedelta

from cryptography.fernet import Fernet
from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.wb_subscription.models.wb_license_instance import (
    EVAL_DAYS, MAX_PARALLEL_EVALS, MISMATCH_GRACE_DAYS,
)

PROD_DOMAIN = 'https://kunde.example.com'
PROD_UUID = 'db-prod-uuid'


class _InstanceSetup:

    @classmethod
    def _setup_license(cls):
        cls._fernet_env_backup = os.environ.get('WB_SUBSCRIPTION_FERNET_KEY')
        os.environ['WB_SUBSCRIPTION_FERNET_KEY'] = Fernet.generate_key().decode('utf-8')
        gen = cls.env['wb.key.generator']
        tmpl = cls.env['product.template'].create({
            'name': 'INST Lizenz-Produkt',
            'wb_is_license_product': True,
            'wb_technical_code': 'INST',
            'wb_instance_limit': 1,
            'wb_activation_grace_days': 90,
        })
        partner = cls.env['res.partner'].create({'name': 'Instanz-Kunde GmbH'})
        code = gen.generate_activation_code()
        cls.license = cls.env['wb.license.key'].create({
            'product_id': tmpl.product_variant_id.id,
            'partner_id': partner.id,
            'state': 'issued',
            'valid_from': fields.Date.today(),
            'valid_to': fields.Date.today() + timedelta(days=365),
            'activation_hash': gen.hash_activation_code(code),
            'activation_expires_at': fields.Datetime.now() + timedelta(days=90),
        })
        cls.license.activate_with_code(code, PROD_DOMAIN, PROD_UUID)

    @classmethod
    def _restore_env(cls):
        if cls._fernet_env_backup is None:
            os.environ.pop('WB_SUBSCRIPTION_FERNET_KEY', None)
        else:
            os.environ['WB_SUBSCRIPTION_FERNET_KEY'] = cls._fernet_env_backup


@tagged('wb_subscription', 'wb_instance')
class TestInstanceResolution(_InstanceSetup, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_license()

    @classmethod
    def tearDownClass(cls):
        cls._restore_env()
        super().tearDownClass()

    def _resolve(self, domain, db_uuid, neutralized, license=None):
        return (license or self.license).resolve_instance(domain, db_uuid, neutralized)

    def _instance(self, db_uuid):
        return self.env['wb.license.instance'].search([
            ('license_id', '=', self.license.id), ('db_uuid', '=', db_uuid)])

    def test_bound_instance_is_unchanged(self):
        self.assertIsNone(self._resolve(PROD_DOMAIN, PROD_UUID, False))
        # Schema und Slash zaehlen nicht als andere Domain.
        self.assertIsNone(self._resolve('kunde.example.com/', PROD_UUID, False))
        self.assertFalse(self.license.instance_ids)

    def test_neutralized_copy_gets_eval_once(self):
        today = fields.Date.today()
        res = self._resolve('https://test.kunde.example.com', 'db-copy-1', True)
        self.assertEqual(res['status'], 'eval')
        self.assertEqual(res['until'], today + timedelta(days=EVAL_DAYS))
        # Zweiter Ping verlaengert nichts.
        res = self._resolve('https://test.kunde.example.com', 'db-copy-1', True)
        self.assertEqual(res['until'], today + timedelta(days=EVAL_DAYS))
        self.assertEqual(len(self.license.instance_ids), 1)
        self.assertTrue(self.license.event_ids.filtered(
            lambda e: e.event_type == 'instance_eval_started'))

    def test_eval_expires(self):
        self._resolve('https://test.kunde.example.com', 'db-copy-1', True)
        self._instance('db-copy-1').eval_until = fields.Date.today() - timedelta(days=1)
        res = self._resolve('https://test.kunde.example.com', 'db-copy-1', True)
        self.assertEqual(res['status'], 'eval_expired')

    def test_parallel_eval_limit(self):
        for i in range(MAX_PARALLEL_EVALS):
            res = self._resolve(f'https://t{i}.example.com', f'db-copy-{i}', True)
            self.assertEqual(res['status'], 'eval')
        res = self._resolve('https://tx.example.com', 'db-copy-x', True)
        self.assertEqual(res['status'], 'eval_limit')
        self.assertFalse(self._instance('db-copy-x').eval_until)

    def test_not_neutralized_copy_gets_grace_then_blocked(self):
        today = fields.Date.today()
        res = self._resolve('https://kopie.example.com', 'db-copy-2', False)
        self.assertEqual(res['status'], 'mismatch_grace')
        self.assertEqual(res['until'], today + timedelta(days=MISMATCH_GRACE_DAYS))
        self._instance('db-copy-2').mismatch_until = today - timedelta(days=1)
        res = self._resolve('https://kopie.example.com', 'db-copy-2', False)
        self.assertEqual(res['status'], 'mismatch')

    def test_un_neutralized_eval_falls_to_mismatch(self):
        self._resolve('https://test.example.com', 'db-copy-3', True)
        res = self._resolve('https://test.example.com', 'db-copy-3', False)
        self.assertEqual(res['status'], 'mismatch_grace')

    def test_same_db_new_domain_is_mismatch_even_if_neutralized(self):
        res = self._resolve('https://neu.example.com', PROD_UUID, True)
        self.assertEqual(res['status'], 'mismatch_grace')

    def test_inactive_license_is_not_resolved(self):
        self.license.state = 'revoked'
        self.assertIsNone(self._resolve('https://kopie.example.com', 'db-copy-4', True))

    def test_dev_license(self):
        self._resolve('https://test.example.com', 'db-test', True)
        inst = self._instance('db-test')
        inst.action_issue_dev_license()
        test = inst.dev_license_id
        self.assertEqual(test.parent_license_id, self.license)
        self.assertEqual(test.bound_db_uuid, 'db-test')
        self.assertEqual(test.valid_to, self.env['sale.order']._wb_compute_valid_to())

        res = self._resolve('https://test.example.com', 'db-test', True)
        self.assertEqual(res['status'], 'dev')
        res = self._resolve('https://test.example.com', 'db-test', False)
        self.assertEqual(res['status'], 'dev_not_neutralized')

        # Mit dem eigenen Key der DEV-Lizenz gelten dieselben Regeln.
        self.assertIsNone(self._resolve('https://test.example.com', 'db-test', True, test))
        res = self._resolve('https://test.example.com', 'db-test', False, test)
        self.assertEqual(res['status'], 'dev_not_neutralized')
        res = self._resolve(PROD_DOMAIN, PROD_UUID, True, test)
        self.assertEqual(res['status'], 'mismatch')

        with self.assertRaises(UserError):
            inst.action_issue_dev_license()

    def test_dev_license_requires_neutralized_db(self):
        self._resolve('https://kopie.example.com', 'db-copy-5', False)
        with self.assertRaises(UserError):
            self._instance('db-copy-5').action_issue_dev_license()


@tagged('wb_subscription', 'wb_instance', 'post_install', '-at_install')
class TestCheckEndpointInstance(_InstanceSetup, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_license()

    @classmethod
    def tearDownClass(cls):
        cls._restore_env()
        super().tearDownClass()

    def _check(self, domain, db_uuid, **extra):
        params = dict(key=self.license.name, domain=domain, db_uuid=db_uuid, **extra)
        resp = self.url_open(
            '/api/license/check',
            data=json.dumps({'jsonrpc': '2.0', 'method': 'call', 'params': params}),
            headers={'Content-Type': 'application/json'},
        )
        return resp.json()['result']

    def test_bound_instance_has_no_instance_block(self):
        res = self._check(PROD_DOMAIN, PROD_UUID)
        self.assertEqual(res['state'], 'active')
        self.assertNotIn('instance', res)

    def test_eval_maps_to_active(self):
        res = self._check('https://test.example.com', 'db-http-1', is_neutralized=True)
        self.assertEqual(res['state'], 'active')
        self.assertEqual(res['instance']['status'], 'eval')
        self.assertEqual(res['valid_to'], res['instance']['until'])

    def test_missing_flag_counts_as_not_neutralized(self):
        res = self._check('https://kopie.example.com', 'db-http-2')
        self.assertEqual(res['state'], 'grace')
        self.assertEqual(res['instance']['status'], 'mismatch_grace')
        self.assertEqual(res['grace_until'], res['instance']['until'])

    def test_string_flag_is_not_trusted(self):
        res = self._check('https://kopie.example.com', 'db-http-3', is_neutralized='true')
        self.assertEqual(res['instance']['status'], 'mismatch_grace')

    def test_blocked_maps_to_expired(self):
        self._check('https://kopie.example.com', 'db-http-4')
        self.env['wb.license.instance'].search([('db_uuid', '=', 'db-http-4')]).mismatch_until = (
            fields.Date.today() - timedelta(days=1))
        res = self._check('https://kopie.example.com', 'db-http-4')
        self.assertEqual(res['state'], 'expired')
        self.assertEqual(res['instance']['status'], 'mismatch')
