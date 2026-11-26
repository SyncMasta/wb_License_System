"""Test per Klick und Benutzergrenze (DECISIONS.md #94, #95), mit Fake-Server."""

from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.wb_license_client.models.wb_license_client import KEY_PARAM_PREFIX, _PING_SPERRE

KEY = 'WB-TRIA-abcdef12AB'


def _no_real_server(env):
    """Never reach the production license server from tests."""
    icp = env['ir.config_parameter'].sudo()
    icp.set_param('wb_license_client.server_url', 'http://127.0.0.1:9')
    icp.set_param('wb_license_client.disable_install_registry', 'True')


@tagged('wb_license_client', 'wb_trial')
class TestTrialWizard(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Meldungen auf Englisch prüfen, unabhängig von der Sprache des Admins
        cls.env = cls.env(context=dict(cls.env.context, lang='en_US'))
        cls.Client = cls.env['wb.license.client']
        cls.ICP = cls.env['ir.config_parameter'].sudo()
        _no_real_server(cls.env)
        cls.ICP.set_param('web.base.url', 'https://kunde.example.com')
        cls.env.user.email = 'admin@kunde.example.com'
        cls.env.company.name = 'Kunde GmbH'

    def setUp(self):
        super().setUp()
        self.addCleanup(_PING_SPERRE.clear)
        self.sent = []

    def _fake(self, answer, status=200):
        def fake_request(endpoint, payload, **kwargs):
            self.sent.append((endpoint, dict(payload)))
            return answer, status
        return patch.object(type(self.Client), '_do_request', side_effect=fake_request)

    def _wizard(self, **vals):
        values = {'mode': 'trial', 'product_code': 'TRIA', 'trial_consent': True}
        values.update(vals)
        return self.env['wb.license.activate.wizard'].create(values)

    def _ok(self, **extra):
        answer = {
            'status': 'ok', 'trial_id': 'TRL/0001', 'key': KEY, 'state': 'trial',
            'product_code': 'TRIA', 'valid_from': fields.Date.today().isoformat(),
            'valid_to': (fields.Date.today() + timedelta(days=7)).isoformat(),
            'max_users': 5,
        }
        answer.update(extra)
        return answer

    def _info(self):
        return self.env['wb.license.info'].search([
            ('product_code', '=', 'TRIA'), ('company_id', '=', self.env.company.id)])

    def test_defaults_from_user_and_company(self):
        wizard = self._wizard()
        self.assertEqual(wizard.trial_contact_email, 'admin@kunde.example.com')
        self.assertEqual(wizard.trial_contact_name, self.env.user.name)
        self.assertEqual(wizard.trial_company_name, 'Kunde GmbH')
        self.assertEqual(wizard.trial_domain, 'https://kunde.example.com')
        self.assertFalse(self.env['wb.license.activate.wizard'].create(
            {'product_code': 'TRIA'}).trial_consent)

    def test_no_consent_sends_nothing(self):
        wizard = self._wizard(trial_consent=False)
        with self._fake(self._ok()):
            with self.assertRaises(UserError):
                wizard.action_start_trial()
        self.assertEqual(self.sent, [])
        self.assertFalse(self.ICP.get_param(KEY_PARAM_PREFIX + 'TRIA'))

    def test_trial_stores_and_activates_key(self):
        with self._fake(self._ok()):
            result = self._wizard().action_start_trial()
        self.assertEqual(result['params']['type'], 'success')
        endpoint, payload = self.sent[0]
        self.assertEqual(endpoint, '/api/license/trial')
        self.assertEqual(payload['product_code'], 'TRIA')
        self.assertEqual(payload['contact_email'], 'admin@kunde.example.com')
        self.assertEqual(payload['company_name'], 'Kunde GmbH')
        self.assertEqual(payload['domain'], 'https://kunde.example.com')
        self.assertEqual(payload['db_uuid'], self.ICP.get_param('database.uuid'))
        self.assertEqual(self.ICP.get_param(KEY_PARAM_PREFIX + 'TRIA'), KEY)
        info = self._info()
        self.assertEqual(info.state, 'trial')
        self.assertEqual(info.key, KEY)
        self.assertEqual(info.valid_to, fields.Date.today() + timedelta(days=7))
        self.assertEqual(info.max_users, 5)
        self.assertTrue(info.is_valid)
        self.assertIn('TRIA', info.user_message)
        # Kein weiterer Server-Aufruf: der Cache ist frisch
        with self._fake({'state': 'expired'}):
            self.assertEqual(self.Client._wb_license_max_users('TRIA'), 5)
        self.assertEqual(len(self.sent), 1)

    def test_server_unreachable_no_half_license(self):
        with self._fake(None, status=0):
            with self.assertRaises(UserError) as err:
                self._wizard().action_start_trial()
        self.assertIn('cannot be reached', str(err.exception))
        self.assertFalse(self.ICP.get_param(KEY_PARAM_PREFIX + 'TRIA'))
        self.assertFalse(self._info().key)

    def test_second_trial_rejected_with_clear_message(self):
        for code, text in (('EMAIL_LIMIT_REACHED', 'email address'),
                           ('DOMAIN_LIMIT_REACHED', 'domain'),
                           ('DATABASE_LIMIT_REACHED', 'database'),
                           ('TRIAL_ALREADY_USED', 'already been used')):
            with self.subTest(code=code), self._fake({'error': code}):
                with self.assertRaises(UserError) as err:
                    self._wizard().action_start_trial()
                self.assertIn(text, str(err.exception))
        self.assertFalse(self.ICP.get_param(KEY_PARAM_PREFIX + 'TRIA'))

    def test_pending_stores_nothing(self):
        with self._fake({'status': 'pending', 'trial_id': 'TRL/0002'}):
            result = self._wizard().action_start_trial()
        self.assertEqual(result['params']['type'], 'warning')
        self.assertFalse(self.ICP.get_param(KEY_PARAM_PREFIX + 'TRIA'))

    def test_invalid_key_from_server_is_not_stored(self):
        for bad in ('kaputt', 'WB-ANDR-abcdef12AB', ''):
            with self.subTest(key=bad), self._fake(self._ok(key=bad)):
                with self.assertRaises(UserError):
                    self._wizard().action_start_trial()
        self.assertFalse(self.ICP.get_param(KEY_PARAM_PREFIX + 'TRIA'))

    def test_existing_key_blocks_trial(self):
        self.Client._store_key('TRIA', 'WB-TRIA-00000000AA')
        with self._fake(self._ok()):
            with self.assertRaises(UserError):
                self._wizard().action_start_trial()
        self.assertEqual(self.sent, [])

    def test_required_fields(self):
        with self._fake(self._ok()):
            with self.assertRaises(UserError):
                self._wizard(trial_contact_email='kaputt').action_start_trial()
            with self.assertRaises(UserError):
                self._wizard(trial_company_name=' ').action_start_trial()
        self.assertEqual(self.sent, [])

    def test_only_admin_can_start_trial(self):
        # Kein eigener Testbenutzer: in Test-DBs mit weiteren Modulen (Pflichtfelder an
        # res.users) scheitert das Anlegen; die Gruppenprüfung wird hier simuliert.
        client = self.Client.with_user(self.env.ref('base.user_admin'))
        with self._fake(self._ok()), \
                patch.object(type(self.env.user), 'has_group', return_value=False):
            with self.assertRaises(AccessError):
                client.start_trial('TRIA', 'X', 'x@example.com', 'X GmbH')
        self.assertEqual(self.sent, [])
        self.assertFalse(self.ICP.get_param(KEY_PARAM_PREFIX + 'TRIA'))

    def test_info_button_opens_trial_mode(self):
        info = self.env['wb.license.info'].create({'product_code': 'TRIA', 'state': 'unlicensed'})
        action = info.action_open_trial_wizard()
        self.assertEqual(action['context']['default_mode'], 'trial')
        self.assertEqual(action['context']['default_product_code'], 'TRIA')


@tagged('wb_license_client', 'wb_trial')
class TestTrialExpiryAndMaxUsers(TransactionCase):

    def setUp(self):
        super().setUp()
        self.addCleanup(_PING_SPERRE.clear)
        _no_real_server(self.env)
        self.Client = self.env['wb.license.client']

    def _info(self, **vals):
        values = {'product_code': 'TRIA', 'company_id': self.env.company.id, 'state': 'unlicensed'}
        values.update(vals)
        return self.env['wb.license.info'].create(values)

    def test_trial_valid_until_end_date(self):
        today = fields.Date.today()
        self.assertTrue(self._info(state='trial', valid_to=today).is_valid)
        info = self.env['wb.license.info'].search([('product_code', '=', 'TRIA')])
        info.valid_to = today - timedelta(days=1)
        self.assertFalse(info.is_valid)

    def test_trial_expiry_from_server(self):
        info = self._info(state='trial', valid_to=fields.Date.today())
        info.apply_server_response({'state': 'expired', 'valid_to': fields.Date.today().isoformat()})
        self.assertEqual(info.state, 'expired')
        self.assertFalse(info.is_valid)

    def test_trial_stays_trial_when_server_down(self):
        info = self._info(state='trial', valid_to=fields.Date.today() - timedelta(days=1),
                          last_server_check=fields.Datetime.now())
        info.record_check_failure(0, None)
        self.assertEqual(info.state, 'trial')
        self.assertFalse(info.is_valid)

    def test_server_response_stores_max_users(self):
        info = self._info()
        info.apply_server_response({'state': 'active', 'max_users': 20})
        self.assertEqual(info.max_users, 20)
        info.apply_server_response({'state': 'active', 'max_users': 'kaputt'})
        self.assertEqual(info.max_users, 0)
        info.apply_server_response({'state': 'active', 'max_users': -3})
        self.assertEqual(info.max_users, 0)
        info.apply_server_response({'state': 'active'})
        self.assertEqual(info.max_users, 0)

    def test_max_users_helper(self):
        self.assertIsNone(self.Client._wb_license_max_users('TRIA'))
        self.Client._store_key('TRIA', 'WB-TRIA-abcdef12AB')
        info = self.env['wb.license.info'].search([('product_code', '=', 'TRIA')])
        info.write({'state': 'active', 'max_users': 5, 'last_server_check': fields.Datetime.now()})
        self.assertEqual(self.Client._wb_license_max_users('TRIA'), 5)
        info.max_users = 0
        self.assertEqual(self.Client._wb_license_max_users('TRIA'), 0)
        info.state = 'expired'
        self.assertIsNone(self.Client._wb_license_max_users('TRIA'))

    def test_activation_stores_max_users(self):
        with patch.object(type(self.Client), '_do_request',
                          return_value=({'status': 'ok', 'state': 'active',
                                         'valid_to': '2026-12-31', 'max_users': 20}, 200)):
            info = self.Client.activate_license('TRIA', 'WB-TRIA-abcdef12AB',
                                                'ABCDE-FGHJK-LMNPQ-RSTUV-WXY23')
        self.assertEqual(info.max_users, 20)
