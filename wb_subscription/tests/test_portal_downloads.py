# -*- coding: utf-8 -*-
"""Tests für /my/downloads — authentifizierter Tarball-Download mit Multi-Version.

Deckt:
* Anonymer Zugriff: Redirect zu Login (auth='user')
* Eingeloggter Customer ohne Lizenz: leere Liste / 404 für Direktzugriff
* Eingeloggter Customer sieht eigene Lizenz auf /my/downloads
* /my/downloads/<lic> zeigt Versions-History für eigene Lizenz
* /my/downloads/<lic>/<rel> liefert spezifische Release-Tarball
* Download legt 'download'-Event an + erhöht download_count
* Foreign license → 404 auf beiden Routen
* Expired license → 404 auf beiden Routen
* Archivierte Release → 404 beim Stream
* Release eines anderen Produkts → 404 beim Stream
"""

import base64
import os
from datetime import timedelta

from cryptography.fernet import Fernet
from odoo import fields
from odoo.tests import HttpCase, tagged


@tagged('wb_subscription', 'wb_portal_downloads', '-at_install', 'post_install')
class TestPortalDownloads(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._fernet_env_backup = os.environ.get('WB_SUBSCRIPTION_FERNET_KEY')
        os.environ['WB_SUBSCRIPTION_FERNET_KEY'] = (
            Fernet.generate_key().decode('utf-8'))

        cls.gen = cls.env['wb.key.generator']
        cls.Release = cls.env['wb.product.release']

        cls.product_template = cls.env['product.template'].create({
            'name': 'TEST Lizenz-Produkt',
            'wb_is_license_product': True,
            'wb_technical_code': 'TEST',
            'wb_module_technical_name': 'wb_test_module',
            'wb_instance_limit': 1,
            'wb_activation_grace_days': 90,
        })
        cls.product = cls.product_template.product_variant_id

        cls.tarball_payload_v1 = b'tarball-v1-content'
        cls.tarball_payload_v2 = b'tarball-v2-content'

        cls.release_v1 = cls.Release.create({
            'product_tmpl_id': cls.product_template.id,
            'version': '19.0.1.0.0',
            'filename': 'wb_test_module-19.0.1.0.0.tar.gz',
            'state': 'published',
        })
        cls.attachment_v1 = cls.env['ir.attachment'].create({
            'name': cls.release_v1.filename,
            'res_model': 'wb.product.release',
            'res_id': cls.release_v1.id,
            'datas': base64.b64encode(cls.tarball_payload_v1),
        })
        cls.release_v1.attachment_id = cls.attachment_v1.id

        cls.release_v2 = cls.Release.create({
            'product_tmpl_id': cls.product_template.id,
            'version': '19.0.1.2.0',
            'filename': 'wb_test_module-19.0.1.2.0.tar.gz',
            'state': 'published',
        })
        cls.attachment_v2 = cls.env['ir.attachment'].create({
            'name': cls.release_v2.filename,
            'res_model': 'wb.product.release',
            'res_id': cls.release_v2.id,
            'datas': base64.b64encode(cls.tarball_payload_v2),
        })
        cls.release_v2.attachment_id = cls.attachment_v2.id
        cls.release_v2.action_set_current()

        cls.partner = cls.env['res.partner'].create({
            'name': 'Test-Kunde GmbH',
            'email': 'kunde@example.com',
        })
        cls.portal_user = cls.env['res.users'].with_context(
            no_reset_password=True).create({
                'name': 'Test-Portal-User',
                'login': 'kunde@example.com',
                'email': 'kunde@example.com',
                'password': 'kunde-test-pw',
                'partner_id': cls.partner.id,
                'groups_id': [(6, 0, [cls.env.ref('base.group_portal').id])],
            })

        cls.other_partner = cls.env['res.partner'].create({
            'name': 'Anderer Kunde',
            'email': 'other@example.com',
        })

        cls.license = cls._make_active_license(cls.partner, 'db-uuid-portal-1')

    @classmethod
    def tearDownClass(cls):
        if cls._fernet_env_backup is None:
            os.environ.pop('WB_SUBSCRIPTION_FERNET_KEY', None)
        else:
            os.environ['WB_SUBSCRIPTION_FERNET_KEY'] = cls._fernet_env_backup
        super().tearDownClass()

    @classmethod
    def _make_active_license(cls, partner, db_uuid):
        code = cls.gen.generate_activation_code()
        license = cls.env['wb.license.key'].create({
            'product_id': cls.product.id,
            'partner_id': partner.id,
            'state': 'issued',
            'valid_from': fields.Date.today(),
            'valid_to': fields.Date.today() + timedelta(days=365),
            'activation_hash': cls.gen.hash_activation_code(code),
            'activation_expires_at': fields.Datetime.now() + timedelta(days=90),
        })
        license.activate_with_code(code, 'kunde.example.com', db_uuid)
        return license

    def test_anonymous_get_downloads_redirects_to_login(self):
        response = self.url_open('/my/downloads', allow_redirects=False)
        self.assertIn(response.status_code, (301, 302, 303))

    def test_logged_in_customer_sees_their_license_in_list(self):
        self.authenticate('kunde@example.com', 'kunde-test-pw')
        response = self.url_open('/my/downloads')
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.license.name, response.text)
        self.assertIn('19.0.1.2.0', response.text)

    def test_license_releases_page_lists_all_published(self):
        self.authenticate('kunde@example.com', 'kunde-test-pw')
        response = self.url_open(f'/my/downloads/{self.license.id}')
        self.assertEqual(response.status_code, 200)
        self.assertIn('19.0.1.0.0', response.text)
        self.assertIn('19.0.1.2.0', response.text)

    def test_download_specific_release_returns_correct_tarball(self):
        self.authenticate('kunde@example.com', 'kunde-test-pw')
        response = self.url_open(
            f'/my/downloads/{self.license.id}/{self.release_v1.id}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, self.tarball_payload_v1)
        self.assertIn(
            self.release_v1.filename,
            response.headers.get('Content-Disposition', ''),
        )

    def test_download_logs_event_and_increments_count(self):
        self.authenticate('kunde@example.com', 'kunde-test-pw')
        before_count = self.release_v2.download_count
        before_events = self.env['wb.license.event'].search_count([
            ('license_id', '=', self.license.id),
            ('event_type', '=', 'download'),
        ])
        self.url_open(
            f'/my/downloads/{self.license.id}/{self.release_v2.id}')
        self.release_v2.invalidate_recordset()
        self.assertEqual(self.release_v2.download_count, before_count + 1)
        after_events = self.env['wb.license.event'].search_count([
            ('license_id', '=', self.license.id),
            ('event_type', '=', 'download'),
        ])
        self.assertEqual(after_events, before_events + 1)

    def test_foreign_license_releases_page_returns_404(self):
        foreign_license = self._make_active_license(
            self.other_partner, 'db-uuid-portal-other')
        self.authenticate('kunde@example.com', 'kunde-test-pw')
        response = self.url_open(f'/my/downloads/{foreign_license.id}')
        self.assertEqual(response.status_code, 404)

    def test_foreign_license_download_returns_404(self):
        foreign_license = self._make_active_license(
            self.other_partner, 'db-uuid-portal-other-2')
        self.authenticate('kunde@example.com', 'kunde-test-pw')
        response = self.url_open(
            f'/my/downloads/{foreign_license.id}/{self.release_v2.id}')
        self.assertEqual(response.status_code, 404)

    def test_expired_license_returns_404(self):
        self.license.write({'state': 'expired'})
        self.authenticate('kunde@example.com', 'kunde-test-pw')
        list_resp = self.url_open(f'/my/downloads/{self.license.id}')
        self.assertEqual(list_resp.status_code, 404)
        download_resp = self.url_open(
            f'/my/downloads/{self.license.id}/{self.release_v2.id}')
        self.assertEqual(download_resp.status_code, 404)

    def test_archived_release_download_returns_404(self):
        self.release_v1.action_archive_release()
        self.authenticate('kunde@example.com', 'kunde-test-pw')
        response = self.url_open(
            f'/my/downloads/{self.license.id}/{self.release_v1.id}')
        self.assertEqual(response.status_code, 404)

    def test_release_of_other_product_returns_404(self):
        other_template = self.env['product.template'].create({
            'name': 'Anderes Lizenz-Produkt',
            'wb_is_license_product': True,
            'wb_technical_code': 'OTHR',
        })
        other_release = self.Release.create({
            'product_tmpl_id': other_template.id,
            'version': '19.0.1.0.0',
            'state': 'published',
        })
        attachment = self.env['ir.attachment'].create({
            'name': 'other.tar.gz',
            'res_model': 'wb.product.release',
            'res_id': other_release.id,
            'datas': b'ZmFrZQ==',
        })
        other_release.attachment_id = attachment.id

        self.authenticate('kunde@example.com', 'kunde-test-pw')
        response = self.url_open(
            f'/my/downloads/{self.license.id}/{other_release.id}')
        self.assertEqual(response.status_code, 404)
