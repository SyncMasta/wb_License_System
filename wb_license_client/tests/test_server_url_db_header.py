"""Tests: Standard-URL des Lizenzservers und Header X-Odoo-Database."""
from unittest.mock import MagicMock, patch

from odoo.tests import TransactionCase, tagged

PARAM_DB = 'wb_license_client.server_database'
PARAM_URL = 'wb_license_client.server_url'


@tagged('wb_license_client', 'wb_server_url')
class TestServerUrlUndDbHeader(TransactionCase):

    def setUp(self):
        super().setUp()
        self.client = self.env['wb.license.client']
        self.params = self.env['ir.config_parameter'].sudo()

    def _header(self):
        antwort = MagicMock(status_code=200)
        antwort.json.return_value = {'result': {'ok': True}}
        with patch('odoo.addons.wb_license_client.models.wb_license_client.requests.post',
                   return_value=antwort) as post:
            self.client._do_request('/api/license/check', {'a': 1})
        return post.call_args.kwargs['headers']

    def test_header_fehlt_standardmaessig(self):
        self.params.set_param(PARAM_DB, '')
        self.assertNotIn('X-Odoo-Database', self._header())

    def test_header_nie_mit_eigener_db(self):
        self.params.search([('key', '=', PARAM_DB)]).unlink()
        self.assertNotIn('X-Odoo-Database', self._header())

    def test_header_aus_parameter(self):
        self.params.set_param(PARAM_DB, 'Main')
        self.assertEqual(self._header()['X-Odoo-Database'], 'Main')

    def test_standard_url_aus_datendatei(self):
        rec = self.env.ref('wb_license_client.config_server_url')
        self.assertEqual(rec.value, 'https://my.wissen-beratung.de')

    def test_standard_url_im_code(self):
        self.params.search([('key', '=', PARAM_URL)]).unlink()
        self.assertEqual(self.client._get_server_url(), 'https://my.wissen-beratung.de')

    def _migrieren(self, wert):
        modul = self._lade_migration()
        self.params.set_param(PARAM_URL, wert)
        modul.migrate(self.env.cr, '19.0.2.5.2')
        return self.params.get_param(PARAM_URL)

    def _lade_migration(self):
        import importlib.util
        import os
        import odoo.addons.wb_license_client as pkg
        pfad = os.path.join(os.path.dirname(pkg.__file__), 'migrations',
                            '19.0.2.6.0', 'post-migration.py')
        spec = importlib.util.spec_from_file_location('wb_lc_mig_2_6_0', pfad)
        modul = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modul)
        return modul

    def test_migration_alte_standards(self):
        self.assertEqual(self._migrieren('https://wissen-beratung.de'),
                         'https://my.wissen-beratung.de')
        self.assertEqual(self._migrieren('https://www.wissen-beratung.de'),
                         'https://my.wissen-beratung.de')

    def test_migration_eigene_werte_bleiben(self):
        for wert in ('https://lizenz.example.com', 'https://wissen-beratung.de/',
                     'https://my.wissen-beratung.de'):
            self.assertEqual(self._migrieren(wert), wert)
