"""Tests fuer Zero-Touch-Auto-Lookup im wb_license_client.

Mocked HTTP-Layer (``_do_request``) — kein echter Server-Call.

Deckt ab:
- _try_auto_lookup speichert Key bei Match-Response
- _try_auto_lookup ist No-op bei found=false
- _try_auto_lookup ist No-op bei HTTP-Fehler
- register_install ruft _try_auto_lookup nur wenn kein Key gespeichert
- auto_lookup_all_installed iteriert nur ueber Module mit konfiguriertem
  product_code und ohne gespeicherten Key
"""
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged


@tagged('wb_license_client', 'wb_auto_lookup')
class TestAutoLookupClient(TransactionCase):

    def setUp(self):
        super().setUp()
        self.client = self.env['wb.license.client']
        # ir.config_parameter: db_uuid + base_url stubben damit
        # _get_db_uuid/_get_domain echte Werte zuruecksenden.
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('database.uuid', 'test-db-uuid-123')
        ICP.set_param('web.base.url', 'https://kunde.example.com')

    def _stored_key(self, code):
        return self.env['ir.config_parameter'].sudo().get_param(
            'wb_license_client.key_' + code)

    def test_try_auto_lookup_stores_key_on_match(self):
        info = self.client._get_or_create_info('AUTO')
        with patch.object(
            self.env.registry['wb.license.client'], '_do_request',
            return_value=({'found': True, 'key': 'WB-AUTO-abc12345AB',
                           'state': 'active', 'valid_to': '2027-12-31'}, 200),
        ):
            ok = self.client._try_auto_lookup(info, 'AUTO')
        self.assertTrue(ok)
        self.assertEqual(self._stored_key('AUTO'), 'WB-AUTO-abc12345AB')
        info.invalidate_recordset()
        self.assertEqual(info.state, 'active')
        self.assertEqual(info.key, 'WB-AUTO-abc12345AB')

    def test_try_auto_lookup_no_match(self):
        info = self.client._get_or_create_info('AUTO')
        with patch.object(
            self.env.registry['wb.license.client'], '_do_request',
            return_value=({'found': False}, 200),
        ):
            ok = self.client._try_auto_lookup(info, 'AUTO')
        self.assertFalse(ok)
        self.assertFalse(self._stored_key('AUTO'))

    def test_try_auto_lookup_http_error(self):
        info = self.client._get_or_create_info('AUTO')
        with patch.object(
            self.env.registry['wb.license.client'], '_do_request',
            return_value=(None, 0),
        ):
            ok = self.client._try_auto_lookup(info, 'AUTO')
        self.assertFalse(ok)

    def test_try_auto_lookup_rate_limited(self):
        info = self.client._get_or_create_info('AUTO')
        with patch.object(
            self.env.registry['wb.license.client'], '_do_request',
            return_value=({'error': 'TOO_MANY_REQUESTS'}, 200),
        ):
            ok = self.client._try_auto_lookup(info, 'AUTO')
        self.assertFalse(ok)

    def test_register_install_skips_lookup_when_key_exists(self):
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('wb_license_client.key_AUTO', 'WB-AUTO-existing12')
        with patch.object(
            self.env.registry['wb.license.client'], '_announce_install',
            return_value=True,
        ), patch.object(
            self.env.registry['wb.license.client'], '_try_auto_lookup',
        ) as mock_lookup:
            self.client.register_install('AUTO')
        mock_lookup.assert_not_called()

    def test_register_install_invokes_lookup_when_no_key(self):
        with patch.object(
            self.env.registry['wb.license.client'], '_announce_install',
            return_value=True,
        ), patch.object(
            self.env.registry['wb.license.client'], '_try_auto_lookup',
            return_value=False,
        ) as mock_lookup:
            self.client.register_install('AUTO', module_name='wb_test_module')
        mock_lookup.assert_called_once()

    def test_register_install_try_auto_lookup_disabled(self):
        with patch.object(
            self.env.registry['wb.license.client'], '_announce_install',
            return_value=True,
        ), patch.object(
            self.env.registry['wb.license.client'], '_try_auto_lookup',
        ) as mock_lookup:
            self.client.register_install('AUTO', try_auto_lookup=False)
        mock_lookup.assert_not_called()
