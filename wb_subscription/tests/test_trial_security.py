# -*- coding: utf-8 -*-
"""Härtung nach dem Security-Review vom 26.11.2026 (DECISIONS.md #97).

I1 exakter E-Mail-Vergleich, kein Zusammenführen mit fremden Kontakten
I2 ein Fehlercode für „Test nicht möglich“, Wiederholung nur aus derselben DB
I3 Domain-Regel nur für öffentliche Hostnamen, Kollision → Prüfung
I4 PRODUCT_MISMATCH bei /check und /activate
I6 Zustimmung Pflicht, Nachweis am Antrag, Aufbewahrungsfristen
I7 /order: Variante Pflicht bei mehreren Varianten, API-Key konstantzeitig
"""

from datetime import timedelta

from odoo import fields
from odoo.tests import HttpCase, TransactionCase, tagged

from .test_trial_and_max_users import DOMAIN, UUID_A, UUID_B, _PaketSetup, _TrialHttpBase

UUID_C = '11111111-2222-3333-4444-777777777777'


@tagged('wb_subscription', 'wb_trial', 'post_install', '-at_install')
class TestTrialSecurity(_TrialHttpBase, HttpCase):

    # ---------------------------------------------------------------- I1/I2
    def test_wildcard_email_does_not_replay_victim_key(self):
        self._trial()
        res = self._trial(contact_email='%@kunde-trial.example.com')
        self.assertEqual(res, {'error': 'TRIAL_NOT_AVAILABLE'})
        res = self._trial(contact_email='_rika@kunde-trial.example.com')
        self.assertEqual(res, {'error': 'TRIAL_NOT_AVAILABLE'})

    def test_replay_only_from_same_database(self):
        first = self._trial()
        res = self._trial(_from_db=UUID_B)
        self.assertEqual(res, {'error': 'TRIAL_NOT_AVAILABLE'})
        self.assertNotIn('key', res)
        res = self._trial(_from_db='')
        self.assertEqual(res, {'error': 'TRIAL_NOT_AVAILABLE'})
        self.assertEqual(self._trial()['key'], first['key'])

    def test_replay_needs_exact_domain(self):
        self._trial()
        res = self._trial(domain='https://www.kunde-trial.example.com')
        self.assertEqual(res, {'error': 'TRIAL_NOT_AVAILABLE'})

    def test_no_merge_into_existing_partner(self):
        kunde = self.env['res.partner'].create({
            'name': 'Bestandskunde', 'email': 'erika@kunde-trial.example.com'})
        self._trial()
        trial = self.env['wb.license.trial.request'].search([('db_uuid', '=', UUID_A)])
        self.assertTrue(trial.partner_id)
        self.assertNotEqual(trial.partner_id, kunde)
        self.assertEqual(trial.license_id.partner_id, trial.partner_id)

    def test_email_stored_lowercase(self):
        self._trial(contact_email='Erika@Kunde-Trial.Example.com')
        trial = self.env['wb.license.trial.request'].search([('db_uuid', '=', UUID_A)])
        self.assertEqual(trial.contact_email, 'erika@kunde-trial.example.com')

    # -------------------------------------------------------------------- I3
    def test_private_hosts_rely_on_database_only(self):
        for i, (host, db) in enumerate((
                ('http://localhost:8069', UUID_A), ('http://localhost:8069', UUID_B),
                ('http://192.168.1.5:8069', UUID_C))):
            with self.subTest(host=host, db=db):
                res = self._trial(contact_email=f'p{i}@example.com', domain=host, db_uuid=db)
                self.assertEqual(res['status'], 'ok')
        self.assertTrue(self.env['wb.license.trial.request']._is_public_host('kunde.example.com'))
        for host in ('localhost', '127.0.0.1', '10.0.0.3', '192.168.1.5', '172.16.0.1',
                     'odoo.local', 'fe80::1', '::1', 'odoo', 'printer.lan', 'odoo.internal'):
            with self.subTest(host=host):
                self.assertFalse(self.env['wb.license.trial.request']._is_public_host(host))

    # -------------------------------------------------------------------- I4
    def test_check_rejects_product_mismatch(self):
        key = self._trial()['key']
        res = self._post('/api/license/check', key=key, product_code='ZZZZ',
                         domain=DOMAIN, db_uuid=UUID_A)
        self.assertEqual(res, {'error': 'PRODUCT_MISMATCH'})
        res = self._post('/api/license/check', key=key, product_code='TRIA',
                         domain=DOMAIN, db_uuid=UUID_A)
        self.assertEqual(res['state'], 'trial')

    def test_activate_rejects_product_mismatch(self):
        gen = self.env['wb.key.generator']
        code = gen.generate_activation_code()
        lic = self.env['wb.license.key'].create({
            'product_id': self.variants['Starter'].id,
            'partner_id': self.env['res.partner'].create({'name': 'Aktivierer'}).id,
            'state': 'issued',
            'valid_from': fields.Date.today(),
            'valid_to': fields.Date.today() + timedelta(days=30),
            'activation_hash': gen.hash_activation_code(code),
            'activation_expires_at': fields.Datetime.now() + timedelta(days=90),
        })
        res = self._post('/api/license/activate', key=lic.name, activation_code=code,
                         product_code='ZZZZ', domain=DOMAIN, db_uuid=UUID_A,
                         confirm_eula=True, confirm_terms=True, confirm_privacy=True,
                         confirm_no_refund=True)
        self.assertEqual(res, {'error': 'PRODUCT_MISMATCH'})
        self.assertEqual(lic.state, 'issued')

    # -------------------------------------------------------------------- I6
    def test_consent_required(self):
        for extra in ({'consent': False}, {'consent': 'true'}, {'privacy_version': ''}):
            with self.subTest(extra=extra):
                self.assertEqual(self._trial(**extra), {'error': 'MISSING_CONSENT'})
        self.assertFalse(self.env['wb.license.trial.request'].search([('db_uuid', '=', UUID_A)]))

    def test_consent_is_recorded(self):
        self._trial()
        trial = self.env['wb.license.trial.request'].search([('db_uuid', '=', UUID_A)])
        self.assertTrue(trial.consent_at)
        self.assertEqual(trial.consent_version, '2026-11-26')

    # -------------------------------------------------------------------- I7
    def _order(self, api_key='geheim-123', **extra):
        params = {'contact_email': 'Besteller@Kunde.example.com', 'contact_name': 'B',
                  'product_code': 'TRIA', 'domain': DOMAIN}
        params.update(extra)
        return self._post('/api/wb_subscription/order', _headers={'X-API-Key': api_key}, **params)

    def test_order_requires_variant_and_api_key(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'wb_subscription.webshop_api_key', 'geheim-123')
        self.assertEqual(self._order(api_key='falsch'), {'error': 'UNAUTHORIZED'})
        self.assertEqual(self._order(), {'error': 'AMBIGUOUS_PRODUCT'})
        fremd = self.env['product.product'].create({'name': 'Kein Lizenzprodukt'})
        self.assertEqual(self._order(product_id=fremd.id), {'error': 'PRODUCT_NOT_FOUND'})
        res = self._order(product_id=self.variants['Team'].id)
        self.assertEqual(res['status'], 'ok')
        order = self.env['sale.order'].browse(res['order_id'])
        self.assertEqual(order.order_line.product_id, self.variants['Team'])
        self.variants['Starter'].default_code = 'TRIA-S'
        res = self._order(default_code='TRIA-S')
        self.assertEqual(self.env['sale.order'].browse(res['order_id']).order_line.product_id,
                         self.variants['Starter'])

    def test_order_partner_match_is_exact(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'wb_subscription.webshop_api_key', 'geheim-123')
        kunde = self.env['res.partner'].create({'name': 'K', 'email': 'besteller@kunde.example.com'})
        res = self._order(product_id=self.variants['Team'].id)
        self.assertEqual(res['partner_id'], kunde.id)
        res = self._order(product_id=self.variants['Team'].id, contact_email='%@kunde.example.com')
        self.assertNotEqual(res['partner_id'], kunde.id)


@tagged('wb_subscription', 'wb_trial', 'wb_retention')
class TestTrialRetention(_PaketSetup, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_products()

    @classmethod
    def tearDownClass(cls):
        cls._restore_env()
        super().tearDownClass()

    def _request(self, days_old, state='pending', **vals):
        values = {
            'product_id': self.variants['Starter'].id, 'contact_name': 'Erika Muster',
            'contact_email': f'e{days_old}{state}@example.com', 'company_name': 'Firma',
            'contact_phone': '+49 1', 'domain': f'https://k{days_old}{state}.example.com',
            'ip_address': '203.0.113.9', 'state': state, 'expected_use_case': 'Text',
        }
        values.update(vals)
        rec = self.env['wb.license.trial.request'].create(values)
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE wb_license_trial_request SET create_date = NOW() - (%s * INTERVAL '1 day') "
            "WHERE id = %s", (days_old, rec.id))
        rec.invalidate_recordset()
        return rec

    def test_ip_anonymised_after_90_days(self):
        alt, neu = self._request(100), self._request(10)
        self.env['wb.license.trial.request']._cron_apply_retention()
        self.assertFalse(alt.ip_address)
        self.assertEqual(neu.ip_address, '203.0.113.9')
        self.assertEqual(alt.contact_name, 'Erika Muster')

    def test_contact_anonymised_after_12_months_except_converted(self):
        alt = self._request(400, state='rejected')
        konvertiert = self._request(400, state='converted')
        self.env['wb.license.trial.request']._cron_apply_retention()
        self.assertNotEqual(alt.contact_name, 'Erika Muster')
        self.assertNotIn('e400rejected', alt.contact_email)
        self.assertFalse(alt.contact_phone)
        self.assertFalse(alt.expected_use_case)
        self.assertEqual(konvertiert.contact_name, 'Erika Muster')

    def test_retention_configurable(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'wb_subscription.retention_trial_ip_days', '0')
        self.env['ir.config_parameter'].sudo().set_param(
            'wb_subscription.retention_trial_contact_days', '200')
        rec = self._request(100)
        self.env['wb.license.trial.request']._cron_apply_retention()
        # IP-Stufe abgeschaltet, Kontaktfrist noch nicht erreicht
        self.assertEqual(rec.ip_address, '203.0.113.9')
        self.assertEqual(rec.contact_name, 'Erika Muster')
        self.env['ir.config_parameter'].sudo().set_param(
            'wb_subscription.retention_trial_contact_days', '30')
        self.env['wb.license.trial.request']._cron_apply_retention()
        self.assertNotEqual(rec.contact_name, 'Erika Muster')
        self.assertTrue(rec.anonymised)
        self.assertFalse(rec.ip_address)
