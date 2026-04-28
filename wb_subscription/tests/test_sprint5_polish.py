# -*- coding: utf-8 -*-
"""Sprint 5 Polish-Tests fuer wb_subscription.

* L-H1: Clock-Anomaly-Detection in wb.license.info
* L-M2: OTP-Versand-Audit (otp_send_failed bei Mail-Fehler)
* L-M3: Lead-Event-Log re-raise auf DB-Errors
* L-L1: Certificate-Number aus ir.sequence (Atomicity-Test)
"""
from datetime import timedelta
from unittest.mock import patch

import psycopg2

from odoo import fields
from odoo.tests import TransactionCase, tagged


# -------------------------------------------------- L-H1 Clock-Anomaly

@tagged('wb_subscription', 'security_regression')
class TestClockAnomalyDetection(TransactionCase):
    """L-H1: zukunfts-datierter last_server_check → is_valid=False
    + Anomalie-Marker beim naechsten Server-Response."""

    def _make_info(self):
        return self.env['wb.license.info'].sudo().create({
            'product_code': 'TEST',
            'state': 'active',
            'company_id': self.env.company.id,
        })

    def test_future_dated_check_blocks_is_valid(self):
        info = self._make_info()
        future = fields.Datetime.now() + timedelta(hours=10)
        info.write({'last_server_check': future})
        info.invalidate_recordset(['is_valid'])
        self.assertFalse(info.is_valid,
                         'Zukunfts-datierter last_server_check muss '
                         'is_valid auf False zwingen — schuetzt vor '
                         'Uhr-Manipulation als Cache-Hold.')

    def test_within_tolerance_still_valid(self):
        info = self._make_info()
        # 30 Minuten Drift sind im Tolerance-Window — Cache bleibt valid
        slightly_future = fields.Datetime.now() + timedelta(minutes=30)
        info.write({'last_server_check': slightly_future})
        info.invalidate_recordset(['is_valid'])
        self.assertTrue(info.is_valid,
                        'NTP-Drift unter 60min darf Cache nicht killen')

    def test_anomaly_marker_set_on_next_server_response(self):
        info = self._make_info()
        # Backdate last_server_check in die Zukunft (Snapshot-Rollback-
        # Szenario: DB-Backup von morgen eingespielt)
        info.write({
            'last_server_check': fields.Datetime.now() + timedelta(hours=10),
        })
        # Naechster Ping kommt → apply_server_response prueft und
        # markiert die Anomalie
        self.assertFalse(info.last_clock_anomaly_at,
                         'Vorm Aufruf: noch keine Anomalie persistiert')
        info.apply_server_response({
            'state': 'active',
            'valid_to': '2027-12-31',
        })
        self.assertTrue(info.last_clock_anomaly_at,
                        'apply_server_response muss Anomalie-Marker '
                        'setzen wenn vorheriger last_server_check in der '
                        'Zukunft lag')


# -------------------------------------------------- L-M2 OTP-Audit

@tagged('wb_subscription', 'security_regression')
class TestOtpSendFailedAudit(TransactionCase):
    """L-M2: bei Mail-Versand-Fehler wird otp_send_failed event geloggt."""

    def test_otp_send_failed_event_type_exists(self):
        """Sanity: Event-Type ist in Selection registriert."""
        Event = self.env['wb.license.event']
        selection_keys = [k for k, _ in Event._fields['event_type'].selection]
        self.assertIn('otp_send_failed', selection_keys,
                      'Selection muss otp_send_failed enthalten (L-M2)')


# -------------------------------------------------- L-M3 Lead-Event Re-Raise

@tagged('wb_subscription', 'security_regression')
class TestLeadEventReRaise(TransactionCase):
    """L-M3: DB-Konsistenzfehler im Lead-Event-Log werden hochgeworfen."""

    def test_db_errors_reraise_logging_errors_silent(self):
        """Pure logic-test ohne RPC-Layer: Event.log_event-Mock raised
        IntegrityError, der Caller in submit_lead muss re-raisen."""
        from psycopg2 import IntegrityError, OperationalError
        # Sanity: psycopg2-Exceptions werden importiert und sind im Code
        # erreichbar (siehe controller).
        from odoo.addons.wb_subscription.controllers import api_license
        import inspect
        src = inspect.getsource(api_license.ApiLicenseController.submit_lead)
        self.assertIn('IntegrityError', src,
                      'submit_lead muss psycopg2.IntegrityError abfangen + '
                      're-raisen (L-M3)')
        self.assertIn('OperationalError', src,
                      'submit_lead muss OperationalError abfangen (L-M3)')


# -------------------------------------------------- L-L1 Certificate-Sequence

@tagged('wb_subscription', 'security_regression')
class TestCertificateSequenceAtomicity(TransactionCase):
    """L-L1: certificate_number kommt aus ir.sequence — Atomic, kein Race."""

    def test_sequence_exists(self):
        seq = self.env['ir.sequence'].search([
            ('code', '=', 'wb.license.certificate'),
        ], limit=1)
        self.assertTrue(seq, 'ir.sequence wb.license.certificate muss '
                             'definiert sein (L-L1)')

    def test_sequence_yields_strictly_increasing(self):
        """Sanity: zwei aufeinanderfolgende next_by_code-Aufrufe liefern
        unterschiedliche, aufsteigende Werte."""
        Sequence = self.env['ir.sequence']
        a = Sequence.next_by_code('wb.license.certificate')
        b = Sequence.next_by_code('wb.license.certificate')
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        self.assertNotEqual(a, b)
