"""Tests: nach einem fehlgeschlagenen Ping wartet check_license 15 Minuten.

Ohne diese Sperre pingte jeder Aufruf einer gegateten Methode erneut (bis zum
Timeout), solange der Lizenz-Server nicht erreichbar war, weil
``last_server_check`` nur bei Erfolg weiterrückt.
"""
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import TransactionCase, tagged

from odoo.addons.wb_license_client.models import wb_license_client as modul
from odoo.addons.wb_license_client.models.wb_license_client import PING_RETRY_MINUTES


@tagged('wb_license_client', 'wb_ping_backoff')
class TestPingBackoff(TransactionCase):

    def setUp(self):
        super().setUp()
        # Speicher-Sperre je Worker ist Modulzustand: vor und nach jedem Test leeren
        modul._PING_SPERRE.clear()
        self.addCleanup(modul._PING_SPERRE.clear)
        self.client = self.env['wb.license.client']
        self.env['ir.config_parameter'].sudo().set_param(
            'wb_license_client.key_BKOF', 'WB-BKOF-abc12345AB')
        self.info = self.client._get_or_create_info('BKOF')
        # Letzter Erfolg vor 2 Tagen: Cache ist älter als 24 h, ein Ping ist fällig
        self.info.write({
            'state': 'active',
            'last_server_check': fields.Datetime.now() - timedelta(days=2),
            'last_check_success': True,
        })

    def _request(self, antwort):
        return patch.object(
            self.env.registry['wb.license.client'], '_do_request', return_value=antwort)

    def test_failed_ping_blocks_retry_for_15_minutes(self):
        self.assertEqual(PING_RETRY_MINUTES, 15)
        with self._request((None, 0)) as request:
            erste = self.client.check_license('BKOF')
            zweite = self.client.check_license('BKOF')
        self.assertEqual(request.call_count, 1, "Innerhalb von 15 min nicht erneut pingen.")
        self.assertTrue(erste.last_failed_check_at)
        # Gespeicherter Zustand gilt weiter (min_cache_age_days unverändert)
        self.assertEqual(zweite.state, 'unknown')
        self.assertTrue(zweite.is_valid)

    def test_retry_after_15_minutes(self):
        self.info.last_failed_check_at = fields.Datetime.now() - timedelta(minutes=16)
        with self._request((None, 0)) as request:
            self.client.check_license('BKOF')
        self.assertEqual(request.call_count, 1)

    def test_success_clears_failure_mark(self):
        self.info.last_failed_check_at = fields.Datetime.now() - timedelta(minutes=16)
        with self._request(({'state': 'active', 'valid_to': '2030-12-31'}, 200)) as request:
            info = self.client.check_license('BKOF')
        self.assertEqual(request.call_count, 1)
        self.assertFalse(info.last_failed_check_at)
        self.assertEqual(info.state, 'active')

    def test_cache_age_policy_unchanged(self):
        """Old cache (> min_cache_age_days) stays invalid while pings are throttled."""
        self.info.write({
            'state': 'unknown',
            'last_server_check': fields.Datetime.now() - timedelta(days=40),
            'last_failed_check_at': fields.Datetime.now() - timedelta(minutes=1),
        })
        with self._request((None, 0)) as request:
            info = self.client.check_license('BKOF')
        self.assertEqual(request.call_count, 0)
        self.assertFalse(info.is_valid)

    def test_sperre_ueberlebt_rollback(self):
        """Der DB-Merker geht bei Rollback verloren, die Speicher-Sperre nicht."""
        with self._request((None, 0)) as request:
            self.client.check_license('BKOF')
            # Rollback: DB-Merker weg, Cache wieder "Ping fällig"
            self.info.last_failed_check_at = False
            self.client.check_license('BKOF')
        self.assertEqual(request.call_count, 1, "Speicher-Sperre muss den zweiten Ping verhindern.")

    def test_sperre_endet_nach_15_minuten(self):
        key = (self.env.cr.dbname, 'BKOF')
        with self._request((None, 0)) as request:
            self.client.check_license('BKOF')
            self.info.last_failed_check_at = False
            modul._PING_SPERRE[key] -= PING_RETRY_MINUTES * 60 + 1
            self.client.check_license('BKOF')
        self.assertEqual(request.call_count, 2)

    def test_erfolg_loescht_speicher_sperre(self):
        key = (self.env.cr.dbname, 'BKOF')
        modul._PING_SPERRE[key] = modul.time.monotonic() - PING_RETRY_MINUTES * 60 - 1
        with self._request(({'state': 'active', 'valid_to': '2030-12-31'}, 200)):
            self.client.check_license('BKOF')
        self.assertNotIn(key, modul._PING_SPERRE)
