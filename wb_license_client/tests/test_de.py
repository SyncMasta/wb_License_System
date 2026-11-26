from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('wb_license_client', 'post_install', '-at_install')
class TestDeutsch(TransactionCase):
    """German comes from i18n/de.po at runtime (source language is English)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['res.lang']._activate_lang('de_DE')
        cls.env['ir.module.module']._load_module_terms(['wb_license_client'], ['de_DE'], overwrite=True)
        cls.de = cls.env(context=dict(cls.env.context, lang='de_DE'))

    def test_feld_und_auswahl(self):
        felder = self.de['wb.license.info'].fields_get(['key', 'state'])
        self.assertEqual(felder['key']['string'], 'Lizenzschlüssel')
        self.assertEqual(dict(felder['state']['selection'])['unlicensed'], 'Kein Schlüssel konfiguriert')
        self.assertEqual(self.env['wb.license.info'].fields_get(['key'])['key']['string'], 'License Key')

    def test_meldung(self):
        info = self.de['wb.license.info'].create({'product_code': 'TEST', 'state': 'expired'})
        self.assertIn('abgelaufen', info.user_message)
        self.assertIn('expired', info.with_context(lang='en_US').user_message)

    def test_fehler_testantrag(self):
        client = self.de['wb.license.client']
        with self.assertRaises(UserError) as fehler:
            client._raise_trial_error(200, {'error': 'EMAIL_LIMIT_REACHED'})
        self.assertIn('E-Mail-Adresse', str(fehler.exception))

    def test_ansicht_und_menue(self):
        ansicht = self.env.ref('wb_license_client.wb_license_activate_wizard_view_form')
        self.assertIn('7 Tage kostenlos testen', ansicht.with_context(lang='de_DE').arch_db)
        self.assertIn('Start 7-day free trial', ansicht.with_context(lang='en_US').arch_db)
        self.assertEqual(self.env.ref('wb_license_client.menu_wb_license_client_root')
                         .with_context(lang='de_DE').name, 'WB Lizenzen')
