# -*- coding: utf-8 -*-
"""Sprint 3 Security-Tests fuer wb_subscription.

Deckt die 6 Findings ab:

* L-C1: Replay-Schutz auf /api/license/activate via request_id + Legacy-Bucket
* L-C2: Rate-Limit zweite Achse (key, hour)
* L-C3: _anonymize_partner_name strippt Rechtsform-Suffixe
* L-C4: Portal-Download Whitelist gegen Sub-Kontakt-IDOR
* L-H3: Rate-Limit-Cleanup-Cron try/except (kein Re-Raise)
* L-H4: wb.license.install PII-Felder haben groups=
"""
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import TransactionCase, tagged

from ..controllers.public_verify import _anonymize_partner_name, _is_legal_suffix
from ..controllers.portal_downloads import _user_can_download


# -------------------------------------------------- L-C1 Replay-Schutz

@tagged('wb_subscription', 'security_regression')
class TestActivationReplayDedup(TransactionCase):
    """L-C1: Activation-Request-Dedup."""

    def test_dedup_with_request_id_returns_cached(self):
        """Identische request_id → gespeichertes Payload zurueckgegeben."""
        ActReq = self.env['wb.license.activation_request']
        rid = '11111111-1111-1111-1111-111111111111'
        ActReq.record_result(
            request_id=rid, key='AAAAA-AAAAA-AAAAA-AAAAA-AAAAA',
            domain='kunde.example.com', db_uuid='uuid-1',
            payload={'status': 'ok', 'state': 'active'},
        )
        cached = ActReq.find_replay(
            request_id=rid, key='ANOTHER-KEY-VALUE',
            domain='ignored.example', db_uuid='uuid-different')
        self.assertEqual(cached, {'status': 'ok', 'state': 'active'},
                         'request_id-Match muss gewinnen — andere Felder ignoriert')

    def test_dedup_legacy_bucket_within_hour(self):
        """Ohne request_id → (key, domain, db_uuid)-Match innerhalb 1h."""
        ActReq = self.env['wb.license.activation_request']
        ActReq.record_result(
            request_id=None, key='BBBBB-BBBBB-BBBBB-BBBBB-BBBBB',
            domain='kunde.example.com', db_uuid='uuid-2',
            payload={'status': 'error', 'error': 'WRONG_CODE'},
        )
        cached = ActReq.find_replay(
            request_id=None, key='BBBBB-BBBBB-BBBBB-BBBBB-BBBBB',
            domain='kunde.example.com', db_uuid='uuid-2')
        self.assertEqual(cached, {'status': 'error', 'error': 'WRONG_CODE'})

    def test_dedup_legacy_bucket_different_uuid_not_cached(self):
        """Anderer db_uuid → keine Replay-Treffer (ist legitim ein neuer Tenant)."""
        ActReq = self.env['wb.license.activation_request']
        ActReq.record_result(
            request_id=None, key='CCCCC-CCCCC-CCCCC-CCCCC-CCCCC',
            domain='kunde.example.com', db_uuid='uuid-A',
            payload={'status': 'ok'},
        )
        cached = ActReq.find_replay(
            request_id=None, key='CCCCC-CCCCC-CCCCC-CCCCC-CCCCC',
            domain='kunde.example.com', db_uuid='uuid-B')
        self.assertIsNone(cached,
                          'andere db_uuid muss eine echte Activation erlauben')

    def test_dedup_no_match_no_cache(self):
        """Wenn nichts passt: None — Caller laeuft normal weiter."""
        ActReq = self.env['wb.license.activation_request']
        cached = ActReq.find_replay(
            request_id='99999999-9999-9999-9999-999999999999',
            key='unknown', domain='unknown', db_uuid='unknown')
        self.assertIsNone(cached)

    def test_cleanup_cron_drops_old_records(self):
        """TTL-Cleanup loescht > 7 Tage alte Records."""
        ActReq = self.env['wb.license.activation_request']
        rec_old = ActReq.sudo().create({
            'request_id': 'old-uuid',
            'key': 'OLD-KEY',
            'result_state': 'ok',
            'result_payload': '{}',
        })
        # back-date via direct SQL — write() respektiert create_date nicht
        self.env.cr.execute(
            "UPDATE wb_license_activation_request SET create_date = %s WHERE id = %s",
            (fields.Datetime.now() - timedelta(days=10), rec_old.id),
        )
        # Plus einer der bleiben soll
        rec_new = ActReq.sudo().create({
            'request_id': 'new-uuid',
            'key': 'NEW-KEY',
            'result_state': 'ok',
            'result_payload': '{}',
        })
        ActReq._cron_cleanup()
        ActReq.invalidate_model()
        self.assertFalse(ActReq.sudo().browse(rec_old.id).exists(),
                         'Alter Eintrag muss weg sein')
        self.assertTrue(ActReq.sudo().browse(rec_new.id).exists(),
                        'Neuer Eintrag muss bleiben')


# -------------------------------------------------- L-C2 Per-Key Rate-Limit

@tagged('wb_subscription', 'security_regression')
class TestPerKeyRateLimit(TransactionCase):
    """L-C2: Bucket 'key:'+key existiert als zweite Achse."""

    def test_per_key_bucket_independent_from_ip_bucket(self):
        """Counter mit Prefix 'key:' und ohne sind getrennt."""
        Rate = self.env['wb.rate.limit.entry']
        # IP-Bucket bis Limit fuellen
        for _ in range(5):
            self.assertTrue(Rate.check_and_increment(
                'activate:1.2.3.4', 'activate', 5, 3600))
        self.assertFalse(Rate.check_and_increment(
            'activate:1.2.3.4', 'activate', 5, 3600))
        # Key-Bucket darf trotzdem noch
        self.assertTrue(Rate.check_and_increment(
            'activate_key:key:KEY-XYZ', 'activate_key', 5, 3600))


# -------------------------------------------------- L-C3 Anonymize

@tagged('wb_subscription', 'security_regression')
class TestAnonymizeSuffixStrip(TransactionCase):
    """L-C3: _anonymize_partner_name laesst keine Rechtsform mehr durchsickern."""

    def test_strips_gmbh(self):
        self.assertEqual(_anonymize_partner_name('Müller GmbH'), 'M…')

    def test_strips_ag(self):
        self.assertEqual(_anonymize_partner_name('Schwarz AG'), 'S…')

    def test_strips_ug_kg_ohg(self):
        self.assertEqual(_anonymize_partner_name('Beispiel UG'), 'B…')
        self.assertEqual(_anonymize_partner_name('Beispiel KG'), 'B…')
        self.assertEqual(_anonymize_partner_name('Beispiel oHG'), 'B…')

    def test_strips_us_suffixes(self):
        self.assertEqual(_anonymize_partner_name('Acme Inc.'), 'A…')
        self.assertEqual(_anonymize_partner_name('Acme LLC'), 'A…')
        self.assertEqual(_anonymize_partner_name('Acme Corp.'), 'A…')
        self.assertEqual(_anonymize_partner_name('Acme Ltd.'), 'A…')

    def test_strips_compound_gmbh_co_kg(self):
        self.assertEqual(
            _anonymize_partner_name('Acme Holdings GmbH & Co. KG'), 'A…')

    def test_solo_name_no_leak(self):
        """Ein-Wort-Firma wird auf Initial reduziert — keine Laenge geleakt."""
        self.assertEqual(_anonymize_partner_name('Müller'), 'M…')
        self.assertEqual(_anonymize_partner_name('Karl-Heinz'), 'K…')

    def test_empty_returns_empty(self):
        self.assertEqual(_anonymize_partner_name(''), '')
        self.assertEqual(_anonymize_partner_name(None), '')

    def test_only_suffix_returns_mask(self):
        self.assertEqual(_anonymize_partner_name('GmbH'), '***')

    def test_no_length_leak(self):
        """Egal wie lang die Firma — Output ist immer Initial+Ellipsis."""
        short = _anonymize_partner_name('Ax GmbH')
        long = _anonymize_partner_name('Aaaaaaaaaaaaaaaaaaaaaa GmbH')
        self.assertEqual(len(short), len(long),
                         'Output-Laenge darf Firmen-Laenge nicht verraten')

    def test_is_legal_suffix_normalizes(self):
        self.assertTrue(_is_legal_suffix('GmbH'))
        self.assertTrue(_is_legal_suffix('gmbh'))
        self.assertTrue(_is_legal_suffix('Inc.'))
        self.assertTrue(_is_legal_suffix('inc'))
        self.assertFalse(_is_legal_suffix('Müller'))
        self.assertFalse(_is_legal_suffix('Holdings'))


# -------------------------------------------------- L-C4 Portal-IDOR-Whitelist

@tagged('wb_subscription', 'security_regression')
class TestPortalDownloadWhitelist(TransactionCase):
    """L-C4: _user_can_download — IDOR-Schutz."""

    def setUp(self):
        super().setUp()
        self.parent_partner = self.env['res.partner'].create({
            'name': 'Acme GmbH',
            'is_company': True,
            'email': 'acme@example.com',
        })
        self.contact_a = self.env['res.partner'].create({
            'name': 'John Schmidt',
            'parent_id': self.parent_partner.id,
            'email': 'john@acme.example.com',
        })
        self.contact_b = self.env['res.partner'].create({
            'name': 'Mary Schmidt',
            'parent_id': self.parent_partner.id,
            'email': 'mary@acme.example.com',
        })
        self.user_a = self.env['res.users'].create({
            'name': 'John Schmidt',
            'login': 'john_test_lc4',
            'partner_id': self.contact_a.id,
        })
        self.user_b = self.env['res.users'].create({
            'name': 'Mary Schmidt',
            'login': 'mary_test_lc4',
            'partner_id': self.contact_b.id,
        })

        product_tmpl = self.env['product.template'].create({
            'name': 'LC4 License',
            'wb_is_license_product': True,
            'wb_technical_code': 'LCFF',
        })
        self.license = self.env['wb.license.key'].create({
            'product_id': product_tmpl.product_variant_id.id,
            'partner_id': self.contact_a.id,
            'state': 'active',
            'valid_from': fields.Date.today(),
            'valid_to': fields.Date.today() + timedelta(days=365),
            'activation_hash_method': 'bcrypt',
            'activated_at': fields.Datetime.now(),
            'bound_domain': 'kunde.example.com',
            'bound_db_uuid': 'lc4-test-uuid',
        })

    def test_direct_partner_user_can_download(self):
        """user.partner_id == license.partner_id, leere Whitelist → Zugriff."""
        self.assertTrue(_user_can_download(self.user_a, self.license))

    def test_sibling_subcontact_blocked(self):
        """Sub-Kontakt des gleichen commercial_partner ohne Whitelist → BLOCK.
        Das ist der eigentliche IDOR-Fix von L-C4."""
        # Beide haben commercial_partner_id == Acme GmbH
        self.assertEqual(self.contact_a.commercial_partner_id,
                         self.contact_b.commercial_partner_id)
        self.assertFalse(_user_can_download(self.user_b, self.license),
                         'Mary darf NICHT Johns Lizenz laden, '
                         'auch wenn beide unter Acme GmbH sitzen')

    def test_whitelisted_user_can_download(self):
        """User in allowed_portal_user_ids → Zugriff, auch ohne partner-Match."""
        self.license.allowed_portal_user_ids = [(6, 0, [self.user_b.id])]
        self.assertTrue(_user_can_download(self.user_b, self.license))

    def test_whitelist_overrides_partner_match(self):
        """Whitelist gefuellt aber user nicht drin → BLOCK, sogar wenn
        partner direkt matchen wuerde. Strikter Modus."""
        self.license.allowed_portal_user_ids = [(6, 0, [self.user_b.id])]
        self.assertFalse(_user_can_download(self.user_a, self.license),
                         'Whitelist nicht-leer → exakte Liste, kein Fallback')


# -------------------------------------------------- L-H3 Cron-Resilience

@tagged('wb_subscription', 'security_regression')
class TestRateLimitCronResilience(TransactionCase):
    """L-H3: _cron_cleanup_expired darf nie raisen."""

    def test_cron_swallows_exception(self):
        """Wenn der DELETE-SQL fehlschlaegt, der Cron loggt aber wirft nicht."""
        Rate = self.env['wb.rate.limit.entry']
        # Simuliere Exception im cr.execute — der Wrapping sorgt dafuer
        # dass der Cron ohne Re-Raise zurueckkehrt.
        with patch.object(self.env.cr, 'execute',
                          side_effect=Exception('simulated DB error')):
            try:
                Rate._cron_cleanup_expired()
            except Exception as exc:
                self.fail('Cron darf nicht raisen, hat aber: %s' % exc)


# -------------------------------------------------- L-H4 PII-Field-Groups

@tagged('wb_subscription', 'security_regression')
class TestInstallPIIFieldGroups(TransactionCase):
    """L-H4: PII-Felder auf wb.license.install haben groups=manager."""

    def test_pii_fields_declare_manager_group(self):
        Install = self.env['wb.license.install']
        manager_group = 'wb_subscription.group_wb_subscription_manager'
        protected_fields = (
            'contact_email', 'contact_name', 'contact_phone',
            'company_name', 'company_vat',
            'company_street', 'company_zip', 'company_city',
            'company_country_code',
            'lead_notes', 'crm_lead_id', 'partner_id',
            'last_seen_ip', 'last_seen_user_agent',
        )
        for field_name in protected_fields:
            with self.subTest(field=field_name):
                field = Install._fields[field_name]
                self.assertEqual(
                    field.groups, manager_group,
                    "%s.groups soll '%s' sein, ist '%s'" % (
                        field_name, manager_group, field.groups))
