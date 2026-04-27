"""Tests für den Activate-Wizard — Format-Validation + Server-Response-Handling."""

from unittest.mock import patch

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('wb_license_client', 'wb_activate_wizard')
class TestActivateWizard(TransactionCase):

    def test_key_format_validation_rejects_lowercase_prefix(self):
        with self.assertRaises(ValidationError):
            self.env['wb.license.activate.wizard'].create({
                'product_code': 'TEST',
                'key': 'wb-TEST-abcdef12AB',
                'activation_code': 'ABCDE-FGHJK-LMNPQ-RSTUV-WXY23',
            })

    def test_key_format_validation_accepts_valid_key(self):
        # Valide Checksum wird im Activate-Wizard nicht geprüft (nur Format)
        # — Server macht das. Deshalb accept bei formal korrektem Pattern.
        self.env['wb.license.activate.wizard'].create({
            'product_code': 'TEST',
            'key': 'WB-TEST-abcdef12AB',
            'activation_code': 'ABCDE-FGHJK-LMNPQ-RSTUV-WXY23',
        })

    def test_activation_code_format_rejects_wrong_groups(self):
        with self.assertRaises(ValidationError):
            self.env['wb.license.activate.wizard'].create({
                'product_code': 'TEST',
                'key': 'WB-TEST-abcdef12AB',
                'activation_code': 'ABCDE-FGHJK-LMNPQ-RSTUV',  # nur 4 Gruppen
            })

    def test_activation_code_rejects_ambiguous_chars(self):
        with self.assertRaises(ValidationError):
            self.env['wb.license.activate.wizard'].create({
                'product_code': 'TEST',
                'key': 'WB-TEST-abcdef12AB',
                'activation_code': 'ABCDE-FGHJK-LMNPQ-RSTUV-WXY01',  # enthält 0 und 1
            })

    def test_product_code_must_be_4_uppercase(self):
        with self.assertRaises(ValidationError):
            self.env['wb.license.activate.wizard'].create({
                'product_code': 'test',  # lowercase
                'key': 'WB-TEST-abcdef12AB',
                'activation_code': 'ABCDE-FGHJK-LMNPQ-RSTUV-WXY23',
            })

    def test_activate_calls_client_service(self):
        wizard = self.env['wb.license.activate.wizard'].create({
            'product_code': 'TEST',
            'key': 'WB-TEST-abcdef12AB',
            'activation_code': 'ABCDE-FGHJK-LMNPQ-RSTUV-WXY23',
        })
        with patch.object(
            self.env.registry['wb.license.client'], 'activate_license',
            return_value=self.env['wb.license.info'].create({
                'product_code': 'TEST',
                'state': 'active',
                'company_id': self.env.company.id,
            })
        ) as mock_activate:
            result = wizard.action_activate()
            mock_activate.assert_called_once_with('TEST', 'WB-TEST-abcdef12AB',
                                                   'ABCDE-FGHJK-LMNPQ-RSTUV-WXY23')
            self.assertEqual(result.get('tag'), 'display_notification')

    def test_activate_propagates_user_error(self):
        wizard = self.env['wb.license.activate.wizard'].create({
            'product_code': 'TEST',
            'key': 'WB-TEST-abcdef12AB',
            'activation_code': 'ABCDE-FGHJK-LMNPQ-RSTUV-WXY23',
        })
        with patch.object(
            self.env.registry['wb.license.client'], 'activate_license',
            side_effect=UserError("WRONG_CODE-Äquivalent")
        ):
            with self.assertRaises(UserError):
                wizard.action_activate()
