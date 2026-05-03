"""Tests für `wb.license.key.issue_nfr_license`.

NFR (Not For Resale) ist der interne Lizenzweg: WB-eigene Module für
WB-eigene Tenants (z.B. MCP1 auf wissen-beratung.de) werden ohne
Sale-Order und ohne Activation-Code-Flow direkt im aktivierten Zustand
ausgestellt.

Tests pinnen die Hauptinvarianten:
- Pre-activated state ('active') + activated_at gesetzt
- is_nfr=True, sale_order_id=False
- Domain-Bindung greift sofort
- Default 99 Jahre Laufzeit
- Audit-Event 'nfr_issued' wird geloggt
- product_code-Validierung greift (über generate_public_key)
"""

from datetime import date, timedelta

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('wb_subscription', 'wb_nfr')
class TestNfrLicense(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product = cls.env['product.template'].create({
            'name': 'NFR Test-Produkt',
            'wb_is_license_product': True,
            'wb_technical_code': 'NFRT',
            'wb_module_technical_name': 'wb_nfr_test',
            'list_price': 0.0,
            'type': 'service',
        }).product_variant_id
        cls.partner = cls.env['res.partner'].create({
            'name': 'WB Internal Tenant',
            'email': 'internal@wissen-beratung.de',
        })

    def test_nfr_key_is_pre_activated(self):
        key = self.env['wb.license.key'].issue_nfr_license(
            product_code='NFRT',
            bound_domain='https://internal.wissen-beratung.de',
            partner_id=self.partner.id,
        )
        self.assertEqual(len(key), 1)
        self.assertEqual(key.state, 'active')
        self.assertTrue(key.is_nfr)
        self.assertTrue(key.activated_at)
        self.assertEqual(key.activation_hash_method, 'nfr')
        self.assertEqual(key.bound_domain, 'https://internal.wissen-beratung.de')
        self.assertFalse(key.sale_order_id)
        self.assertFalse(key.activation_hash)  # kein Code-Hash bei NFR

    def test_nfr_key_default_validity_99_years(self):
        today = date.today()
        key = self.env['wb.license.key'].issue_nfr_license(
            product_code='NFRT',
            bound_domain='https://default-validity.test',
            partner_id=self.partner.id,
        )
        self.assertEqual(key.valid_from, today)
        # 99 Jahre ≈ 36135 Tage. Toleranz +/-1 für Schaltjahre.
        delta = (key.valid_to - today).days
        self.assertGreaterEqual(delta, 99 * 365 - 1)
        self.assertLessEqual(delta, 99 * 365 + 1)

    def test_nfr_key_custom_validity(self):
        today = date.today()
        key = self.env['wb.license.key'].issue_nfr_license(
            product_code='NFRT',
            bound_domain='https://short.test',
            partner_id=self.partner.id,
            valid_years=2,
        )
        delta = (key.valid_to - today).days
        self.assertGreaterEqual(delta, 2 * 365 - 1)
        self.assertLessEqual(delta, 2 * 365 + 1)

    def test_nfr_key_partner_defaults_to_company(self):
        # Ohne partner_id-Argument: partner = env.company.partner_id (Self-Issue).
        key = self.env['wb.license.key'].issue_nfr_license(
            product_code='NFRT',
            bound_domain='https://self-issue.test',
        )
        self.assertEqual(key.partner_id, self.env.company.partner_id)

    def test_nfr_key_unknown_product_raises(self):
        with self.assertRaises(UserError):
            self.env['wb.license.key'].issue_nfr_license(
                product_code='ZZZZ',
                bound_domain='https://nope.test',
                partner_id=self.partner.id,
            )

    def test_nfr_key_logs_audit_event(self):
        key = self.env['wb.license.key'].issue_nfr_license(
            product_code='NFRT',
            bound_domain='https://audit.test',
            partner_id=self.partner.id,
        )
        events = self.env['wb.license.event'].search([
            ('license_id', '=', key.id),
            ('event_type', '=', 'nfr_issued'),
        ])
        self.assertEqual(len(events), 1)
        self.assertIn('NFRT', events.details or '')
        self.assertIn('https://audit.test', events.details or '')

    def test_nfr_key_with_db_uuid_binds_immediately(self):
        key = self.env['wb.license.key'].issue_nfr_license(
            product_code='NFRT',
            bound_domain='https://prebound.test',
            bound_db_uuid='deadbeef-1234-5678-90ab-feedfacecafe',
            partner_id=self.partner.id,
            instance_limit=3,
        )
        self.assertEqual(key.bound_db_uuid, 'deadbeef-1234-5678-90ab-feedfacecafe')
        self.assertEqual(key.instance_limit, 3)

    def test_nfr_key_alphanumeric_product_code_supported(self):
        # MCP1-Pattern (3 Buchstaben + Ziffer) ist seit dem
        # alphanumerischen Regex-Update zulässig — relevant für die
        # erste reale NFR-Anwendung (wb_odoo_mcp Self-Hosting).
        product_mcp = self.env['product.template'].create({
            'name': 'NFR MCP1 Test',
            'wb_is_license_product': True,
            'wb_technical_code': 'NFR1',
            'wb_module_technical_name': 'wb_nfr_versioned_test',
            'list_price': 0.0,
            'type': 'service',
        }).product_variant_id
        self.assertTrue(product_mcp)  # constraint passed
        key = self.env['wb.license.key'].issue_nfr_license(
            product_code='NFR1',
            bound_domain='https://versioned.test',
            partner_id=self.partner.id,
        )
        self.assertTrue(key.name.startswith('WB-NFR1-'))
