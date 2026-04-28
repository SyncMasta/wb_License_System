# -*- coding: utf-8 -*-
"""Tests für Modul-Versions-Tracking + Multi-Version-Releases.

Deckt:
* installed_module_version-Feld auf wb.license.install schreibbar
* product.template.wb_current_release_id berechnet sich aus Releases
* wb_latest_module_version + wb_has_downloadable_tarball als computed
* action_set_current schaltet is_current zwischen Releases um
* Constraint: zwei is_current für gleiches Produkt → ValidationError
* Constraint: is_current nur für state='published' zulässig
* Event-Typ 'download' ist registriert
"""

import os
from datetime import timedelta

from cryptography.fernet import Fernet
from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('wb_subscription', 'wb_module_version')
class TestModuleVersionCheck(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._fernet_env_backup = os.environ.get('WB_SUBSCRIPTION_FERNET_KEY')
        os.environ['WB_SUBSCRIPTION_FERNET_KEY'] = (
            Fernet.generate_key().decode('utf-8'))

        cls.Install = cls.env['wb.license.install']
        cls.Release = cls.env['wb.product.release']
        cls.gen = cls.env['wb.key.generator']

        cls.product_template = cls.env['product.template'].create({
            'name': 'TEST Lizenz-Produkt',
            'wb_is_license_product': True,
            'wb_technical_code': 'TEST',
            'wb_module_technical_name': 'wb_test_module',
            'wb_instance_limit': 1,
            'wb_activation_grace_days': 90,
        })
        cls.product = cls.product_template.product_variant_id

        cls.partner = cls.env['res.partner'].create({
            'name': 'Test-Kunde GmbH',
            'email': 'admin@kunde.example.com',
        })

    @classmethod
    def tearDownClass(cls):
        if cls._fernet_env_backup is None:
            os.environ.pop('WB_SUBSCRIPTION_FERNET_KEY', None)
        else:
            os.environ['WB_SUBSCRIPTION_FERNET_KEY'] = cls._fernet_env_backup
        super().tearDownClass()

    def _make_active_license(self):
        code = self.gen.generate_activation_code()
        license = self.env['wb.license.key'].create({
            'product_id': self.product.id,
            'partner_id': self.partner.id,
            'state': 'issued',
            'valid_from': fields.Date.today(),
            'valid_to': fields.Date.today() + timedelta(days=365),
            'activation_hash': self.gen.hash_activation_code(code),
            'activation_expires_at': fields.Datetime.now() + timedelta(days=90),
        })
        license.activate_with_code(code, 'kunde.example.com', 'db-uuid-mod-1')
        return license

    def _make_release(self, version, with_attachment=True, set_current=False):
        release = self.Release.create({
            'product_tmpl_id': self.product_template.id,
            'version': version,
            'state': 'published',
        })
        if with_attachment:
            attachment = self.env['ir.attachment'].create({
                'name': f'wb_test_module-{version}.tar.gz',
                'res_model': 'wb.product.release',
                'res_id': release.id,
                'datas': b'ZmFrZQ==',
            })
            release.attachment_id = attachment.id
        if set_current:
            release.action_set_current()
        return release

    # --------------- install record ---------------

    def test_install_record_stores_module_version_via_announce(self):
        install = self.Install.announce(
            'TEST', 'kunde.example.com', 'db-uuid-mod-1')
        install.write({'installed_module_version': '19.0.1.0.0'})
        self.assertEqual(install.installed_module_version, '19.0.1.0.0')

    # --------------- product computed fields ---------------

    def test_no_releases_means_no_current_and_no_version(self):
        self.assertFalse(self.product_template.wb_current_release_id)
        self.assertFalse(self.product_template.wb_latest_module_version)
        self.assertFalse(self.product_template.wb_has_downloadable_tarball)

    def test_current_release_drives_product_version(self):
        self._make_release('19.0.1.0.0', set_current=True)
        self.product_template.invalidate_recordset()
        self.assertEqual(
            self.product_template.wb_latest_module_version, '19.0.1.0.0')
        self.assertTrue(self.product_template.wb_has_downloadable_tarball)

    def test_setting_new_current_replaces_old(self):
        old = self._make_release('19.0.1.0.0', set_current=True)
        new = self._make_release('19.0.1.1.0')
        new.action_set_current()
        old.invalidate_recordset()
        self.assertFalse(old.is_current)
        self.assertTrue(new.is_current)
        self.product_template.invalidate_recordset()
        self.assertEqual(
            self.product_template.wb_latest_module_version, '19.0.1.1.0')

    # --------------- constraints ---------------

    def test_two_is_current_at_same_time_blocked_by_constraint(self):
        """Direktes is_current=True auf zweite Release ohne action_set_current
        löst die Validierungs-Constraint aus."""
        self._make_release('19.0.1.0.0', set_current=True)
        second = self._make_release('19.0.1.1.0')
        with self.assertRaises(ValidationError):
            second.is_current = True

    def test_is_current_requires_published_state(self):
        release = self.Release.create({
            'product_tmpl_id': self.product_template.id,
            'version': '19.0.1.0.0',
            'state': 'draft',
        })
        with self.assertRaises(ValidationError):
            release.is_current = True

    def test_action_set_current_publishes_draft(self):
        release = self.Release.create({
            'product_tmpl_id': self.product_template.id,
            'version': '19.0.1.0.0',
            'state': 'draft',
        })
        attachment = self.env['ir.attachment'].create({
            'name': 'fake.tar.gz',
            'res_model': 'wb.product.release',
            'res_id': release.id,
            'datas': b'ZmFrZQ==',
        })
        release.attachment_id = attachment.id
        release.action_set_current()
        self.assertEqual(release.state, 'published')
        self.assertTrue(release.is_current)

    def test_archive_clears_is_current(self):
        release = self._make_release('19.0.1.0.0', set_current=True)
        release.action_archive_release()
        self.assertEqual(release.state, 'archived')
        self.assertFalse(release.is_current)

    # --------------- event ---------------

    def test_event_type_download_is_registered(self):
        license = self._make_active_license()
        event = self.env['wb.license.event'].log_event(
            license, 'download',
            details={'release_id': 0, 'version': '19.0.1.2.0'},
        )
        self.assertEqual(event.event_type, 'download')
