"""Tests für wb.license.info — Cache-Logik, is_valid-Policy."""

from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('wb_license_client', 'wb_license_info')
class TestLicenseInfo(TransactionCase):

    def _create_info(self, **overrides):
        vals = {
            'product_code': 'TEST',
            'company_id': self.env.company.id,
            'state': 'unlicensed',
        }
        vals.update(overrides)
        return self.env['wb.license.info'].create(vals)

    def test_active_is_valid(self):
        info = self._create_info(state='active')
        self.assertTrue(info.is_valid)

    def test_grace_is_valid(self):
        info = self._create_info(state='grace')
        self.assertTrue(info.is_valid)

    def test_expired_is_not_valid(self):
        info = self._create_info(state='expired')
        self.assertFalse(info.is_valid)

    def test_unlicensed_is_not_valid(self):
        info = self._create_info(state='unlicensed')
        self.assertFalse(info.is_valid)

    def test_unknown_without_prior_check_is_not_valid(self):
        info = self._create_info(state='unknown', last_server_check=False)
        self.assertFalse(info.is_valid)

    def test_unknown_recent_check_is_valid_default_30d(self):
        info = self._create_info(
            state='unknown',
            last_server_check=fields.Datetime.now() - timedelta(days=10),
        )
        self.assertTrue(info.is_valid, "10d alt sollte innerhalb der 30d-Default-Toleranz liegen.")

    def test_unknown_old_check_invalid_default_30d(self):
        info = self._create_info(
            state='unknown',
            last_server_check=fields.Datetime.now() - timedelta(days=35),
        )
        self.assertFalse(info.is_valid)

    def test_unknown_strict_policy_7d(self):
        """Mit min_cache_age_days=7 wird ein 10d alter Cache als unvalid gewertet."""
        info = self._create_info(
            state='unknown',
            last_server_check=fields.Datetime.now() - timedelta(days=10),
            effective_min_cache_age_days=7,
        )
        self.assertFalse(info.is_valid)

    def test_key_masked(self):
        info = self._create_info(key='WB-TELE-a3f28c919K')
        self.assertEqual(info.key_masked, 'WB-TELE-...9K')

    def test_key_masked_short(self):
        info = self._create_info(key='x')
        self.assertEqual(info.key_masked, 'x')

    def test_apply_server_response_sets_state_and_timestamp(self):
        info = self._create_info()
        before = fields.Datetime.now()
        info.apply_server_response({
            'state': 'active',
            'valid_from': '2026-01-01',
            'valid_to': '2026-12-31',
        })
        self.assertEqual(info.state, 'active')
        self.assertTrue(info.last_check_success)
        self.assertGreaterEqual(info.last_server_check, before)

    def test_apply_server_response_bad_state_falls_back_to_unknown(self):
        info = self._create_info(state='active')
        info.apply_server_response({'state': 'garbage'})
        self.assertEqual(info.state, 'unknown')

    def test_record_check_failure_transitions_active_to_unknown(self):
        info = self._create_info(state='active', last_server_check=fields.Datetime.now())
        info.record_check_failure(500, {'error': 'SERVER_DOWN'})
        self.assertEqual(info.state, 'unknown')
        self.assertFalse(info.last_check_success)
        self.assertIn('SERVER_DOWN', info.last_error_message or '')

    def test_record_check_failure_keeps_expired_state(self):
        info = self._create_info(state='expired', last_server_check=fields.Datetime.now())
        info.record_check_failure(0, None)
        self.assertEqual(info.state, 'expired',
                         "Expired darf NICHT zu unknown degradieren — sonst würde der Kunde "
                         "nach Expired + Server-Ausfall plötzlich wieder arbeiten können.")

    def test_user_message_for_grace_contains_grace_until(self):
        info = self._create_info(state='grace',
                                 grace_until=fields.Date.today() + timedelta(days=5))
        self.assertIn(info.grace_until.isoformat(), info.user_message)

    def test_unique_constraint_on_product_company(self):
        from psycopg2 import IntegrityError
        from odoo.tools import mute_logger
        self._create_info(product_code='TEST')
        with mute_logger('odoo.sql_db'), self.assertRaises(IntegrityError):
            self._create_info(product_code='TEST')
