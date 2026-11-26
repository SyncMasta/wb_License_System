"""Härtung nach dem Security-Review vom 26.11.2026 (DECISIONS.md #97), mit Fake-Server.

I4 Schlüssel nur für das eigene Produkt (Präfix, Serverantwort, PRODUCT_MISMATCH)
I5 Admin-Prüfung und nicht per RPC aufrufbare Methoden, Feldrechte am Cache
"""

from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.wb_license_client.models.wb_license_client import (
    KEY_PARAM_PREFIX, WbLicenseClient, _PING_SPERRE,
)

from .test_trial import _no_real_server

CODE = 'ABCDE-FGHJK-LMNPQ-RSTUV-WXY23'


@tagged('wb_license_client', 'wb_security')
class TestClientSecurity(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, lang='en_US'))
        _no_real_server(cls.env)
        cls.Client = cls.env['wb.license.client']
        cls.ICP = cls.env['ir.config_parameter'].sudo()

    def setUp(self):
        super().setUp()
        self.addCleanup(_PING_SPERRE.clear)
        self.sent = []

    def _fake(self, answer, status=200):
        def fake_request(endpoint, payload, **kwargs):
            self.sent.append((endpoint, dict(payload)))
            return answer, status
        return patch.object(type(self.Client), '_do_request', side_effect=fake_request)

    def _info(self, code='TRIA'):
        return self.env['wb.license.info'].search([('product_code', '=', code)])

    # -------------------------------------------------------------------- I4
    def test_activate_refuses_key_of_other_product(self):
        with self._fake({'status': 'ok', 'state': 'active', 'valid_to': '2026-12-31'}):
            with self.assertRaises(UserError):
                self.Client.activate_license('TRIA', 'WB-ANDR-abcdef12AB', CODE)
        self.assertEqual(self.sent, [])
        self.assertFalse(self.ICP.get_param(KEY_PARAM_PREFIX + 'TRIA'))

    def test_activate_sends_product_code(self):
        with self._fake({'status': 'ok', 'state': 'active', 'valid_to': '2026-12-31'}):
            self.Client.activate_license('TRIA', 'WB-TRIA-abcdef12AB', CODE)
        self.assertEqual(self.sent[0][1]['product_code'], 'TRIA')

    def test_auto_lookup_ignores_key_of_other_product(self):
        self.ICP.set_param('web.base.url', 'https://kunde.example.com')
        info = self.Client._get_or_create_info('TRIA')
        with self._fake({'found': True, 'key': 'WB-ANDR-abcdef12AB', 'state': 'active'}):
            self.assertFalse(self.Client._try_auto_lookup(info, 'TRIA'))
        self.assertFalse(self.ICP.get_param(KEY_PARAM_PREFIX + 'TRIA'))

    def test_stored_key_of_other_product_counts_as_expired(self):
        self.Client._store_key('TRIA', 'WB-ANDR-abcdef12AB')
        with self._fake({'state': 'active'}):
            info = self.Client.check_license('TRIA')
        self.assertEqual(self.sent, [])
        self.assertEqual(info.state, 'expired')
        self.assertFalse(info.is_valid)

    def test_answer_for_other_product_counts_as_expired(self):
        self.Client._store_key('TRIA', 'WB-TRIA-abcdef12AB')
        with self._fake({'state': 'active', 'product_code': 'ANDR'}):
            info = self.Client.check_license('TRIA')
        self.assertEqual(info.state, 'expired')
        self.assertFalse(info.is_valid)

    def test_server_product_mismatch_counts_as_expired(self):
        self.Client._store_key('TRIA', 'WB-TRIA-abcdef12AB')
        with self._fake({'error': 'PRODUCT_MISMATCH'}):
            info = self.Client.check_license('TRIA')
        self.assertEqual(info.state, 'expired')

    def test_ping_sends_product_code_and_matching_answer_is_kept(self):
        self.Client._store_key('TRIA', 'WB-TRIA-abcdef12AB')
        with self._fake({'state': 'active', 'product_code': 'TRIA'}):
            info = self.Client.check_license('TRIA')
        self.assertEqual(self.sent[0][1]['product_code'], 'TRIA')
        self.assertEqual(info.state, 'active')

    # -------------------------------------------------------------------- I5
    def _as_non_admin(self):
        return patch.object(type(self.env.user), 'has_group', return_value=False)

    def test_admin_only_methods(self):
        client = self.Client.with_user(self.env.ref('base.user_admin'))
        calls = (
            lambda: client.activate_license('TRIA', 'WB-TRIA-abcdef12AB', CODE),
            lambda: client.request_migration('TRIA', 'https://neu.example.com', 'x', 'Umzug',
                                             'a@example.com'),
            lambda: client.auto_lookup_all_installed(),
            lambda: client.start_trial('TRIA', 'X', 'x@example.com', 'X GmbH'),
        )
        with self._fake({'status': 'ok'}), self._as_non_admin():
            for call in calls:
                with self.subTest(call=call), self.assertRaises(AccessError):
                    call()
        self.assertEqual(self.sent, [])

    def test_not_callable_over_rpc(self):
        for name in ('check_license', 'register_install', 'submit_lead_request',
                     'activate_license', 'request_migration', 'auto_lookup_all_installed',
                     'start_trial'):
            with self.subTest(method=name):
                self.assertTrue(getattr(getattr(WbLicenseClient, name), '_api_private', False))

    def test_server_side_callers_still_work(self):
        # Produktmodule rufen check_license/register_install serverseitig auf
        info = self.Client.check_license('NOKY')
        self.assertEqual(info.state, 'unlicensed')
        with self._fake({'status': 'ok'}):
            self.Client.register_install('NOKY', try_auto_lookup=False)

    def test_cache_fields_admin_only(self):
        felder = self.env['wb.license.info']._fields
        self.assertEqual(felder['key'].groups, 'base.group_system')
        self.assertEqual(felder['server_response_raw'].groups, 'base.group_system')
        info = self.env['wb.license.info'].create({
            'product_code': 'TRIA', 'key': 'WB-TRIA-abcdef12AB', 'state': 'active',
            'last_server_check': fields.Datetime.now()})
        self.assertEqual(info.key_masked, 'WB-TRIA-...AB')
