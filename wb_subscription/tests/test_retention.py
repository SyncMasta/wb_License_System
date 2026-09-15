"""Tests fuer die Aufbewahrungsfristen (DSGVO Art. 5 Abs. 1 lit. e).

Geprueft wird beides: dass Altes tatsaechlich verschwindet UND dass Junges
unangetastet bleibt. Der zweite Teil ist der wichtigere — ein Retention-Cron,
der zu viel loescht, faellt erst auf, wenn die Daten weg sind.
"""

from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('wb_subscription', 'wb_retention')
class TestEventRetention(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Event = cls.env['wb.license.event'].sudo()

    def _event(self, event_type, tage_alt, **kwargs):
        """Legt ein Event an und datiert es per SQL zurueck.

        Der ORM-Weg geht nicht: timestamp hat ein default und wird beim
        create ueberschrieben.
        """
        ev = self.Event.create(dict({
            'event_type': event_type,
            'ip_address': '203.0.113.7',
            'user_agent': 'pytest',
        }, **kwargs))
        self.env.cr.execute(
            "UPDATE wb_license_event SET timestamp = %s WHERE id = %s",
            (fields.Datetime.now() - timedelta(days=tage_alt), ev.id),
        )
        ev.invalidate_recordset()
        return ev

    def test_alte_ip_wird_genullt(self):
        alt = self._event('ping', 120)
        self.Event._cron_apply_retention()
        alt.invalidate_recordset()
        self.assertFalse(alt.ip_address)
        self.assertFalse(alt.user_agent)

    def test_junge_ip_bleibt(self):
        jung = self._event('ping', 10)
        self.Event._cron_apply_retention()
        jung.invalidate_recordset()
        self.assertEqual(jung.ip_address, '203.0.113.7')

    def test_event_bleibt_nach_anonymisierung_bestehen(self):
        """Der Nachweis, DASS etwas passiert ist, bleibt erhalten."""
        alt = self._event('activation', 120)
        self.Event._cron_apply_retention()
        self.assertTrue(alt.exists())
        self.assertEqual(alt.event_type, 'activation')

    def test_sehr_alte_pings_werden_geloescht(self):
        uralt = self._event('ping', 500)
        uralt_id = uralt.id
        self.Event._cron_apply_retention()
        self.assertFalse(self.Event.browse(uralt_id).exists())

    def test_andere_event_typen_werden_nie_geloescht(self):
        """Aktivierungen und Sperrungen sind Audit, keine Masse."""
        for typ in ('activation', 'revoked', 'nfr_issued', 'signature_failed'):
            with self.subTest(typ=typ):
                ev = self._event(typ, 500)
                self.Event._cron_apply_retention()
                self.assertTrue(ev.exists(), "%s wurde faelschlich geloescht" % typ)

    def test_frist_ist_konfigurierbar(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'wb_subscription.retention_event_pii_days', '10')
        ev = self._event('ping', 20)
        self.Event._cron_apply_retention()
        ev.invalidate_recordset()
        self.assertFalse(ev.ip_address)

    def test_null_schaltet_ab_statt_alles_zu_loeschen(self):
        """Ein Tippfehler in der Konfiguration darf kein Massenloeschen sein."""
        self.env['ir.config_parameter'].sudo().set_param(
            'wb_subscription.retention_event_pii_days', '0')
        self.env['ir.config_parameter'].sudo().set_param(
            'wb_subscription.retention_event_ping_days', '0')
        ev = self._event('ping', 9999)
        self.Event._cron_apply_retention()
        ev.invalidate_recordset()
        self.assertTrue(ev.exists())
        self.assertEqual(ev.ip_address, '203.0.113.7')

    def test_unsinniger_wert_faellt_auf_default_zurueck(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'wb_subscription.retention_event_pii_days', 'abc')
        self.assertEqual(
            self.Event._retention_days(
                'wb_subscription.retention_event_pii_days', 90),
            90,
        )


@tagged('wb_subscription', 'wb_retention')
class TestInstallRetention(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Install = cls.env['wb.license.install'].sudo()

    def _install(self, tage_alt, state='unlicensed', **kwargs):
        rec = self.Install.create(dict({
            'product_code': 'TEST',
            'domain': 'https://kunde%d.example.com' % tage_alt,
            'db_uuid': 'uuid-%d' % tage_alt,
            'contact_email': 'kunde@example.com',
            'contact_name': 'Erika Musterfrau',
            'company_name': 'Musterfirma GmbH',
            'last_seen_ip': '203.0.113.9',
            'state': state,
        }, **kwargs))
        rec.write({
            'last_seen_at': fields.Datetime.now() - timedelta(days=tage_alt),
        })
        return rec

    def test_alte_kontaktdaten_werden_geleert(self):
        alt = self._install(900)
        self.Install._cron_apply_retention()
        self.assertFalse(alt.contact_email)
        self.assertFalse(alt.contact_name)
        self.assertFalse(alt.last_seen_ip)

    def test_firmenname_und_kennzahlen_bleiben(self):
        """Ohne Firma und Zaehler waere der Eintrag fuer die Statistik wertlos."""
        alt = self._install(900)
        self.Install._cron_apply_retention()
        self.assertEqual(alt.company_name, 'Musterfirma GmbH')
        self.assertEqual(alt.product_code, 'TEST')
        self.assertTrue(alt.exists())

    def test_junge_eintraege_bleiben_unberuehrt(self):
        jung = self._install(30)
        self.Install._cron_apply_retention()
        self.assertEqual(jung.contact_email, 'kunde@example.com')

    def test_konvertierte_werden_ausgenommen(self):
        """Bei bestehendem Vertrag laeuft die Frist ueber den Partner."""
        kunde = self._install(900, state='converted')
        self.Install._cron_apply_retention()
        self.assertEqual(kunde.contact_email, 'kunde@example.com')

    def test_abschalten_per_null(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'wb_subscription.retention_install_pii_days', '0')
        alt = self._install(9999)
        self.Install._cron_apply_retention()
        self.assertEqual(alt.contact_email, 'kunde@example.com')
