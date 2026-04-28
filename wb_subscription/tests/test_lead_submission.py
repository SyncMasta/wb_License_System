# -*- coding: utf-8 -*-
"""Tests für submit_lead — Lead-/Verkaufschancen-Erzeugung aus Wizard-Payload.

Deckt:
* submit_lead legt neuen Install + crm.lead bei intent='info' an
* submit_lead legt Opportunity (type='opportunity') bei intent='purchase' an
* submit_lead reichert bestehenden Install-Record an (Upsert)
* submit_lead Eskalation 'info' → 'purchase' wandelt Lead zu Opportunity
* submit_lead funktioniert auch ohne crm-Modul (Soft-Dependency)
"""
import os

from cryptography.fernet import Fernet
from odoo.tests import TransactionCase, tagged


@tagged('wb_subscription', 'wb_lead_submission')
class TestSubmitLead(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._fernet_env_backup = os.environ.get('WB_SUBSCRIPTION_FERNET_KEY')
        os.environ['WB_SUBSCRIPTION_FERNET_KEY'] = (
            Fernet.generate_key().decode('utf-8'))
        cls.Install = cls.env['wb.license.install']
        cls.has_crm = 'crm.lead' in cls.env

    @classmethod
    def tearDownClass(cls):
        if cls._fernet_env_backup is None:
            os.environ.pop('WB_SUBSCRIPTION_FERNET_KEY', None)
        else:
            os.environ['WB_SUBSCRIPTION_FERNET_KEY'] = cls._fernet_env_backup
        super().tearDownClass()

    def _payload(self, **overrides):
        base = {
            'product_code': 'BITW',
            'domain': 'kunde.example.com',
            'db_uuid': 'db-uuid-lead-1',
            'intent': 'info',
            'contact_email': 'admin@kunde.de',
            'contact_name': 'Maxa Musterfrau',
            'contact_phone': '+49 30 1234567',
            'company_name': 'Muster AG',
            'company_vat': 'DE123456789',
            'company_street': 'Musterstr. 1',
            'company_zip': '10115',
            'company_city': 'Berlin',
            'company_country_code': 'DE',
            'notes': 'Bitte Rückruf zur Bundle-Konfiguration.',
            'client_version': '19.0.1.0.0',
        }
        base.update(overrides)
        return base

    def test_submit_lead_creates_install(self):
        install = self.Install.submit_lead(self._payload())
        self.assertTrue(install)
        self.assertEqual(install.product_code, 'BITW')
        self.assertEqual(install.contact_name, 'Maxa Musterfrau')
        self.assertEqual(install.company_name, 'Muster AG')
        self.assertEqual(install.lead_intent, 'info')
        # state bleibt 'unlicensed' bei intent=info
        self.assertEqual(install.state, 'unlicensed')

    def test_submit_lead_purchase_sets_qualified_state(self):
        install = self.Install.submit_lead(
            self._payload(intent='purchase', db_uuid='db-uuid-lead-purchase'))
        self.assertEqual(install.lead_intent, 'purchase')
        self.assertEqual(install.state, 'lead_qualified')

    def test_submit_lead_upsert(self):
        first = self.Install.submit_lead(self._payload())
        second = self.Install.submit_lead(
            self._payload(contact_phone='+49 30 9999999'))
        self.assertEqual(first.id, second.id)
        self.assertEqual(second.contact_phone, '+49 30 9999999')
        self.assertEqual(second.announce_count, 2)

    def test_submit_lead_creates_crm_lead_if_available(self):
        if not self.has_crm:
            self.skipTest("crm-Modul nicht installiert — Soft-Dep-Pfad")
        install = self.Install.submit_lead(self._payload())
        self.assertTrue(install.crm_lead_id)
        self.assertEqual(install.crm_lead_id.type, 'lead')

    def test_submit_lead_purchase_creates_opportunity(self):
        if not self.has_crm:
            self.skipTest("crm-Modul nicht installiert — Soft-Dep-Pfad")
        install = self.Install.submit_lead(
            self._payload(intent='purchase', db_uuid='db-uuid-purchase'))
        self.assertTrue(install.crm_lead_id)
        self.assertEqual(install.crm_lead_id.type, 'opportunity')
        self.assertEqual(install.crm_lead_id.priority, '2')

    def test_submit_lead_escalates_lead_to_opportunity(self):
        if not self.has_crm:
            self.skipTest("crm-Modul nicht installiert — Soft-Dep-Pfad")
        install = self.Install.submit_lead(
            self._payload(db_uuid='db-uuid-escalate'))
        self.assertEqual(install.crm_lead_id.type, 'lead')
        self.Install.submit_lead(
            self._payload(intent='purchase', db_uuid='db-uuid-escalate'))
        install.invalidate_recordset()
        self.assertEqual(install.crm_lead_id.type, 'opportunity')

    def test_submit_lead_without_crm_module_is_safe(self):
        if self.has_crm:
            self.skipTest("crm-Modul installiert — Hard-Dep-Pfad")
        install = self.Install.submit_lead(self._payload())
        self.assertTrue(install)
        self.assertFalse(install.crm_lead_id)
