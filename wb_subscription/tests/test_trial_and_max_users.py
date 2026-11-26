# -*- coding: utf-8 -*-
"""Test per Klick (DECISIONS.md #94) und Benutzergrenze je Paket (#95).

Deckt:
* /api/license/trial gibt sofort frei: Schlüssel, Ablauf, Bindung an Domain + DB-UUID
* 1 Test je DB-UUID; gleiche E-Mail/Domain aus anderer DB → Prüfung (pending), kein Schlüssel
* gleicher Antrag erneut → derselbe Schlüssel (kein zweiter Test)
* Missbrauchsverdacht → pending ohne Schlüssel, nach Freigabe liefert ein neuer Klick den Schlüssel
* Rate-Limit je IP, Produkt ohne Testtage
* Test-Schlüssel in einer Kopie: gesperrt, keine Eval
* wb_max_users an der Variante → Lizenz → /api/license/check max_users
"""

import json
import os
import re
from datetime import timedelta

from cryptography.fernet import Fernet
from odoo import fields
from odoo.tests import HttpCase, TransactionCase, tagged

DOMAIN = 'https://kunde-trial.example.com'
UUID_A = '11111111-2222-3333-4444-555555555555'
UUID_B = '11111111-2222-3333-4444-666666666666'
KEY_RE = re.compile(r'^WB-TRIA-[a-f0-9]{8}[A-Z2-7]{2}$')


class _PaketSetup:

    @classmethod
    def _setup_products(cls):
        cls._fernet_env_backup = os.environ.get('WB_SUBSCRIPTION_FERNET_KEY')
        os.environ['WB_SUBSCRIPTION_FERNET_KEY'] = Fernet.generate_key().decode('utf-8')
        attribute = cls.env['product.attribute'].create({
            'name': 'Paket (Test)',
            'create_variant': 'always',
            'value_ids': [(0, 0, {'name': n}) for n in ('Starter', 'Team', 'Business')],
        })
        cls.tmpl = cls.env['product.template'].create({
            'name': 'TRIA Lizenz-Produkt',
            'wb_is_license_product': True,
            'wb_technical_code': 'TRIA',
            'wb_trial_days': 7,
            'attribute_line_ids': [(0, 0, {
                'attribute_id': attribute.id,
                'value_ids': [(6, 0, attribute.value_ids.ids)],
            })],
        })
        cls.variants = {}
        for variant in cls.tmpl.product_variant_ids:
            name = variant.product_template_attribute_value_ids.name
            cls.variants[name] = variant
        cls.variants['Starter'].wb_max_users = 5
        cls.variants['Team'].wb_max_users = 20
        cls.variants['Business'].wb_max_users = 0

    @classmethod
    def _restore_env(cls):
        if cls._fernet_env_backup is None:
            os.environ.pop('WB_SUBSCRIPTION_FERNET_KEY', None)
        else:
            os.environ['WB_SUBSCRIPTION_FERNET_KEY'] = cls._fernet_env_backup


class _TrialHttpBase(_PaketSetup):
    """HTTP helpers for /api/license/trial (mixed into HttpCase classes)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_products()

    @classmethod
    def tearDownClass(cls):
        cls._restore_env()
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        # Alle Tests kommen von 127.0.0.1: IP-Limit hier großzügig, eigener Test unten.
        self.env['ir.config_parameter'].sudo().set_param(
            'wb_subscription.rate_limit_trial_ip', '1000/3600')

    def _post(self, endpoint, _headers=None, **params):
        headers = {'Content-Type': 'application/json'}
        headers.update(_headers or {})
        resp = self.url_open(
            endpoint,
            data=json.dumps({'jsonrpc': '2.0', 'method': 'call', 'params': params}),
            headers=headers,
        )
        return resp.json()['result']

    def _trial(self, _from_db=None, **overrides):
        params = {
            'product_code': 'TRIA',
            'contact_name': 'Erika Muster',
            'contact_email': 'erika@kunde-trial.example.com',
            'company_name': 'Kunde Trial GmbH',
            'domain': DOMAIN,
            'db_uuid': UUID_A,
            'consent': True,
            'privacy_version': '2026-11-26',
        }
        params.update(overrides)
        # Der Client sendet seine DB-UUID zusätzlich im Header X-WB-DB-UUID
        header_db = _from_db if _from_db is not None else params.get('db_uuid') or ''
        return self._post('/api/license/trial', _headers={'X-WB-DB-UUID': header_db}, **params)

    def _licenses(self):
        return self.env['wb.license.key'].search([('product_code', '=', 'TRIA')])


@tagged('wb_subscription', 'wb_trial', 'post_install', '-at_install')
class TestTrialEndpoint(_TrialHttpBase, HttpCase):

    def test_trial_is_approved_immediately(self):
        res = self._trial()
        self.assertEqual(res['status'], 'ok')
        self.assertRegex(res['key'], KEY_RE)
        self.assertEqual(res['state'], 'trial')
        self.assertEqual(res['valid_to'], (fields.Date.today() + timedelta(days=7)).isoformat())
        # Größtes Paket: Business = unbegrenzt
        self.assertEqual(res['max_users'], 0)
        self.assertNotIn('activation_code', res)
        self.assertNotIn('api_secret', res)
        lic = self._licenses()
        self.assertEqual(len(lic), 1)
        self.assertEqual(lic.name, res['key'])
        self.assertEqual(lic.product_id, self.variants['Business'])
        self.assertEqual(lic.bound_domain, DOMAIN)
        self.assertEqual(lic.bound_db_uuid, UUID_A)
        self.assertTrue(lic.activated_fingerprint)
        trial = self.env['wb.license.trial.request'].search([('license_id', '=', lic.id)])
        self.assertEqual(trial.state, 'active')
        self.assertEqual(trial.domain_normalized, 'kunde-trial.example.com')

    def test_trial_days_from_product(self):
        self.tmpl.wb_trial_days = 3
        res = self._trial()
        self.assertEqual(res['valid_to'], (fields.Date.today() + timedelta(days=3)).isoformat())

    def test_product_without_trial(self):
        self.tmpl.wb_trial_days = 0
        self.assertEqual(self._trial()['error'], 'TRIAL_NOT_AVAILABLE')
        self.assertFalse(self._licenses())

    def test_unknown_product(self):
        self.assertEqual(self._trial(product_code='ZZZZ')['error'], 'PRODUCT_NOT_FOUND')

    def test_invalid_input(self):
        self.assertEqual(self._trial(contact_email='kaputt')['error'], 'INVALID_EMAIL')
        self.assertEqual(self._trial(company_name=' ')['error'], 'MISSING_COMPANY')
        self.assertEqual(self._trial(db_uuid='nicht-uuid')['error'], 'INVALID_DB_UUID')
        self.assertFalse(self.env['wb.license.trial.request'].search([]).filtered(
            lambda t: t.product_id.wb_technical_code == 'TRIA'))

    def test_same_request_again_returns_same_key(self):
        first = self._trial()
        second = self._trial(contact_email='ERIKA@kunde-trial.example.com')
        self.assertEqual(second['status'], 'ok')
        self.assertEqual(second['key'], first['key'])
        self.assertEqual(len(self._licenses()), 1)

    def test_second_trial_same_email_other_db_is_reviewed(self):
        self._trial()
        res = self._trial(domain='https://andere.example.com', db_uuid=UUID_B)
        self.assertEqual(res['status'], 'pending')
        self.assertNotIn('key', res)
        self.assertEqual(len(self._licenses()), 1)

    def test_second_trial_same_domain_other_db_is_reviewed(self):
        self._trial()
        # Schema, www. und Groß-/Kleinschreibung zählen nicht als andere Domain
        res = self._trial(contact_email='max@andere.example.com',
                          domain='http://WWW.kunde-trial.example.com/', db_uuid=UUID_B)
        self.assertEqual(res['status'], 'pending')
        self.assertEqual(len(self._licenses()), 1)

    def test_second_trial_same_database_rejected(self):
        self._trial()
        res = self._trial(contact_email='max@andere.example.com',
                          domain='https://andere.example.com')
        self.assertEqual(res, {'error': 'TRIAL_NOT_AVAILABLE'})
        self.assertEqual(len(self._licenses()), 1)

    def test_trial_of_other_product_is_allowed(self):
        self.env['product.template'].create({
            'name': 'TRIB Lizenz-Produkt',
            'wb_is_license_product': True,
            'wb_technical_code': 'TRIB',
            'wb_trial_days': 7,
        })
        self.assertEqual(self._trial()['status'], 'ok')
        res = self._trial(product_code='TRIB')
        self.assertEqual(res['status'], 'ok')
        self.assertTrue(res['key'].startswith('WB-TRIB-'))

    def test_rejected_after_trial_expired(self):
        first = self._trial()
        lic = self._licenses()
        lic.write({'state': 'expired'})
        res = self._trial()
        self.assertEqual(res, {'error': 'TRIAL_NOT_AVAILABLE'})
        self.assertNotIn(first['key'], json.dumps(res))

    def test_abuse_suspicion_is_pending_without_key(self):
        res = self._trial(contact_email='wer@mailinator.com')
        self.assertEqual(res['status'], 'pending')
        self.assertNotIn('key', res)
        self.assertFalse(self._licenses())
        trial = self.env['wb.license.trial.request'].search([('contact_email', '=', 'wer@mailinator.com')])
        self.assertEqual(trial.state, 'pending')
        self.assertIn('mailinator.com', trial.review_reason)
        # Erneuter Klick vor der Freigabe: weiterhin pending
        self.assertEqual(self._trial(contact_email='wer@mailinator.com')['status'], 'pending')
        # Nach manueller Freigabe liefert derselbe Antrag den Schlüssel
        trial.action_approve_and_create_trial()
        res = self._trial(contact_email='wer@mailinator.com')
        self.assertEqual(res['status'], 'ok')
        self.assertEqual(res['key'], trial.license_id.name)

    def test_configured_review_domain(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'wb_subscription.trial_review_email_domains', 'beispiel-wegwerf.de, x.org')
        res = self._trial(contact_email='a@beispiel-wegwerf.de')
        self.assertEqual(res['status'], 'pending')

    def test_database_with_previous_license_is_reviewed(self):
        self.env['wb.license.key'].create({
            'product_id': self.variants['Starter'].id,
            'partner_id': self.env['res.partner'].create({'name': 'Altkunde'}).id,
            'state': 'expired',
            'valid_from': fields.Date.today() - timedelta(days=400),
            'valid_to': fields.Date.today() - timedelta(days=35),
            'activated_at': fields.Datetime.now() - timedelta(days=400),
            'bound_domain': DOMAIN,
            'bound_db_uuid': UUID_A,
        })
        res = self._trial()
        self.assertEqual(res['status'], 'pending')
        self.assertEqual(len(self._licenses()), 1)

    def test_rejected_request_returns_no_key(self):
        self._trial(contact_email='wer@mailinator.com')
        trial = self.env['wb.license.trial.request'].search([('contact_email', '=', 'wer@mailinator.com')])
        trial.action_reject()
        self.assertEqual(trial.state, 'rejected')
        self.assertEqual(self._trial(contact_email='wer@mailinator.com'), {'error': 'TRIAL_NOT_AVAILABLE'})

    def test_ip_rate_limit(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'wb_subscription.rate_limit_trial_ip', '1/3600')
        self.assertEqual(self._trial()['status'], 'ok')
        res = self._trial(contact_email='b@b.example.com', domain='https://b.example.com', db_uuid=UUID_B)
        self.assertEqual(res, {'error': 'TOO_MANY_REQUESTS'})

    def test_trial_key_check_and_copy(self):
        key = self._trial()['key']
        res = self._post('/api/license/check', key=key, domain=DOMAIN, db_uuid=UUID_A)
        self.assertEqual(res['state'], 'trial')
        self.assertEqual(res['max_users'], 0)
        self.assertNotIn('instance', res)
        # Kopie (auch neutralisiert) bekommt keine Eval aus einem Test
        res = self._post('/api/license/check', key=key, domain='https://kopie.example.com',
                         db_uuid=UUID_B, is_neutralized=True)
        self.assertEqual(res['state'], 'expired')
        self.assertEqual(res['instance']['status'], 'mismatch')

    def test_check_reports_max_users(self):
        lic = self.env['wb.license.key'].create({
            'product_id': self.variants['Starter'].id,
            'partner_id': self.env['res.partner'].create({'name': 'Starter-Kunde'}).id,
            'state': 'active',
            'valid_from': fields.Date.today(),
            'valid_to': fields.Date.today() + timedelta(days=30),
            'activated_at': fields.Datetime.now(),
        })
        self.assertEqual(lic.max_users, 5)
        res = self._post('/api/license/check', key=lic.name, domain=DOMAIN, db_uuid=UUID_B)
        self.assertEqual(res['max_users'], 5)


@tagged('wb_subscription', 'wb_max_users')
class TestMaxUsers(_PaketSetup, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_products()

    @classmethod
    def tearDownClass(cls):
        cls._restore_env()
        super().tearDownClass()

    def _license(self, variant, **extra):
        vals = {
            'product_id': variant.id,
            'partner_id': self.env['res.partner'].create({'name': 'Paket-Kunde'}).id,
            'state': 'issued',
            'valid_from': fields.Date.today(),
            'valid_to': fields.Date.today() + timedelta(days=30),
        }
        vals.update(extra)
        return self.env['wb.license.key'].create(vals)

    def test_license_copies_variant_limit(self):
        self.assertEqual(self._license(self.variants['Starter']).max_users, 5)
        self.assertEqual(self._license(self.variants['Team']).max_users, 20)
        self.assertEqual(self._license(self.variants['Business']).max_users, 0)

    def test_explicit_value_wins(self):
        self.assertEqual(self._license(self.variants['Starter'], max_users=7).max_users, 7)

    def test_product_change_does_not_touch_issued_licenses(self):
        lic = self._license(self.variants['Starter'])
        self.assertEqual(lic.max_users, 5)
        self.env.flush_all()
        self.variants['Starter'].wb_max_users = 3
        self.env.flush_all()
        lic.invalidate_recordset(['max_users'])
        self.assertEqual(lic.max_users, 5)

    def test_variant_change_takes_new_limit(self):
        lic = self._license(self.variants['Starter'])
        lic.product_id = self.variants['Team']
        self.assertEqual(lic.max_users, 20)

    def test_trial_variant_is_largest_package(self):
        Product = self.env['product.product']
        self.assertEqual(Product._wb_trial_variant('TRIA'), self.variants['Business'])
        self.variants['Business'].wb_max_users = 50
        self.assertEqual(Product._wb_trial_variant('TRIA'), self.variants['Business'])
        self.variants['Team'].wb_max_users = 0
        self.assertEqual(Product._wb_trial_variant('TRIA'), self.variants['Team'])
        self.assertFalse(Product._wb_trial_variant('NONE'))

    def test_template_field_with_single_variant(self):
        tmpl = self.env['product.template'].create({
            'name': 'EINS Lizenz-Produkt',
            'wb_is_license_product': True,
            'wb_technical_code': 'EINS',
            'wb_max_users': 10,
        })
        self.assertEqual(tmpl.product_variant_id.wb_max_users, 10)
        self.assertEqual(tmpl.wb_max_users, 10)
        # Mehrere Varianten: Template zeigt 0, Werte stehen an den Varianten
        self.assertEqual(self.tmpl.wb_max_users, 0)
