"""Tests für wb.dashboard — KPI-Berechnung."""

from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('wb_subscription', 'wb_dashboard')
class TestDashboard(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product_template = cls.env['product.template'].create({
            'name': 'Dashboard-Test-Produkt',
            'wb_is_license_product': True,
            'wb_technical_code': 'DASH',
            'list_price': 199.0,
        })
        cls.product = cls.product_template.product_variant_id
        cls.partner = cls.env['res.partner'].create({
            'name': 'Dashboard-Test-Kunde',
            'email': 'dash@example.com',
        })

    def _create_license(self, state, **overrides):
        vals = {
            'product_id': self.product.id,
            'partner_id': self.partner.id,
            'state': state,
            'valid_from': fields.Date.today(),
            'valid_to': fields.Date.today() + timedelta(days=365),
        }
        if state in ('active', 'grace', 'expired'):
            vals['activated_at'] = fields.Datetime.now()
        vals.update(overrides)
        return self.env['wb.license.key'].create(vals)

    def test_kpi_data_counts_states_correctly(self):
        self._create_license('active')
        self._create_license('active')
        self._create_license('trial')
        self._create_license('grace')
        self._create_license('issued')

        kpis = self.env['wb.dashboard']._kpi_data()
        self.assertEqual(kpis['active'], 2)
        self.assertEqual(kpis['trial'], 1)
        self.assertEqual(kpis['grace'], 1)
        self.assertEqual(kpis['issued_pending'], 1)

    def test_mrr_arr_calculation(self):
        self._create_license('active')
        self._create_license('active')

        kpis = self.env['wb.dashboard']._kpi_data()
        expected_mrr = (199.0 / 12.0) * 2
        self.assertAlmostEqual(kpis['mrr'], expected_mrr, places=2)
        self.assertAlmostEqual(kpis['arr'], expected_mrr * 12, places=2)

    def test_expiring_30d_filter(self):
        self._create_license(
            'active', valid_to=fields.Date.today() + timedelta(days=20))
        self._create_license(
            'active', valid_to=fields.Date.today() + timedelta(days=100))
        self._create_license(
            'expired', valid_to=fields.Date.today() + timedelta(days=10))

        kpis = self.env['wb.dashboard']._kpi_data()
        self.assertEqual(kpis['expiring_30d'], 1, "Nur active+grace zählen, nicht expired.")

    def test_pending_tickets_only_pending_or_awaiting(self):
        license = self._create_license('issued')
        Ticket = self.env['wb.activation.ticket']
        gen = self.env['wb.key.generator']
        Ticket.create({
            'license_id': license.id,
            'email': 'a@b.de',
            'encrypted_code': b'fake-encrypted-bytes',
            'state': 'pending',
        })
        Ticket.create({
            'license_id': license.id,
            'email': 'a@b.de',
            'encrypted_code': b'fake-encrypted-bytes',
            'state': 'awaiting_otp',
        })
        Ticket.create({
            'license_id': license.id,
            'email': 'a@b.de',
            'state': 'consumed',
        })

        kpis = self.env['wb.dashboard']._kpi_data()
        self.assertEqual(kpis['pending_tickets'], 2)

    def test_pending_migrations_count(self):
        license = self._create_license('active')
        Migration = self.env['wb.license.migration.request']
        Migration.create({
            'license_id': license.id,
            'new_domain': 'new.example.com',
            'new_db_uuid': 'uuid-new',
            'reason': 'test',
            'contact_email': 'a@b.de',
        })
        kpis = self.env['wb.dashboard']._kpi_data()
        self.assertGreaterEqual(kpis['pending_migrations'], 1)
