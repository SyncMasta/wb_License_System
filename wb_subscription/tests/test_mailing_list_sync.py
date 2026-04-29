"""Tests fuer Mailing-List-Auto-Create und Subscribe/Unsubscribe-Sync.

Deckt ab:
- Beim Anlegen eines Lizenz-Produkts wird automatisch eine mailing.list
  mit Name '[<CODE>] <Name>' erzeugt und ans Produkt verlinkt.
- Beim Setzen von wb_is_license_product=True nachtraeglich wird die Liste
  ebenfalls erzeugt.
- Lizenz-State-Wechsel auf 'active' subscribed den Partner.
- State-Wechsel auf 'expired'/'revoked'/'cancelled' setzt opt_out=True
  (ohne Subscription zu loeschen — DSGVO-Audit-Trail).
- 'grace' triggert nichts (Lizenz funktioniert noch).
- Idempotenz: doppelter Subscribe-Aufruf erzeugt keine doppelte
  Subscription.
- Re-Subscribe nach Unsubscribe: opt_out wird auf False gesetzt.
- Bei mehreren Lizenzen pro (partner, product): Unsubscribe nur wenn
  KEINE andere active/grace-Lizenz mehr existiert.
"""

import os

from cryptography.fernet import Fernet
from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('wb_subscription', 'wb_mailing_list_sync', '-at_install', 'post_install')
class TestMailingListSync(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._fernet_env_backup = os.environ.get('WB_SUBSCRIPTION_FERNET_KEY')
        os.environ['WB_SUBSCRIPTION_FERNET_KEY'] = (
            Fernet.generate_key().decode('utf-8'))

        cls.gen = cls.env['wb.key.generator']

        cls.partner = cls.env['res.partner'].create({
            'name': 'Mailing-Test-Kunde GmbH',
            'email': 'mail-test@example.com',
        })

    @classmethod
    def tearDownClass(cls):
        if cls._fernet_env_backup is None:
            os.environ.pop('WB_SUBSCRIPTION_FERNET_KEY', None)
        else:
            os.environ['WB_SUBSCRIPTION_FERNET_KEY'] = cls._fernet_env_backup
        super().tearDownClass()

    def _make_product(self, code='MAIL', is_license=True):
        """Erzeugt ein Lizenz-Produkt — Mailing-List-Auto-Create greift."""
        return self.env['product.template'].create({
            'name': 'Mailing-Test-Produkt %s' % code,
            'wb_is_license_product': is_license,
            'wb_technical_code': code if is_license else False,
            'wb_billing_calendar': 'monthly',
            'list_price': 50.0,
            'type': 'service',
        })

    def _make_license(self, product, partner=None, state='issued'):
        partner = partner or self.partner
        code = self.gen.generate_activation_code()
        return self.env['wb.license.key'].create({
            'product_id': product.product_variant_id.id,
            'partner_id': partner.id,
            'state': state,
            'valid_from': fields.Date.today(),
            'valid_to': fields.Date.today().replace(month=12, day=31),
            'activation_hash': self.gen.hash_activation_code(code),
        })

    def _get_subscription(self, mailing_list, email):
        Contact = self.env['mailing.contact'].sudo()
        Subscription = self.env['mailing.subscription'].sudo()
        contact = Contact.search([('email', '=ilike', email)], limit=1)
        if not contact:
            return self.env['mailing.subscription']
        return Subscription.search([
            ('contact_id', '=', contact.id),
            ('list_id', '=', mailing_list.id),
        ], limit=1)

    # ------------------------------------------------ Auto-Create der Liste

    def test_create_license_product_creates_mailing_list(self):
        product = self._make_product(code='AUTO')
        self.assertTrue(product.wb_mailing_list_id,
            "Beim Anlegen eines Lizenz-Produkts soll eine Mailing-Liste "
            "automatisch erzeugt werden.")
        self.assertEqual(
            product.wb_mailing_list_id.name,
            '[AUTO] %s' % product.name,
            "Liste muss [<CODE>]-Prefix tragen.")

    def test_set_is_license_product_creates_list_retrospectively(self):
        product = self._make_product(code='LATE', is_license=False)
        self.assertFalse(product.wb_mailing_list_id)
        product.write({
            'wb_is_license_product': True,
            'wb_technical_code': 'LATE',
        })
        self.assertTrue(product.wb_mailing_list_id,
            "Nachtraegliches Setzen von wb_is_license_product=True soll "
            "ebenfalls Liste erzeugen.")

    def test_non_license_product_no_list(self):
        product = self._make_product(code=False, is_license=False)
        self.assertFalse(product.wb_mailing_list_id)

    # ------------------------------------------------- Subscribe bei active

    def test_state_active_subscribes_partner(self):
        product = self._make_product(code='SUB1')
        license = self._make_license(product, state='issued')
        self.assertFalse(self._get_subscription(
            product.wb_mailing_list_id, self.partner.email))
        license.write({'state': 'active', 'activated_at': fields.Datetime.now()})
        sub = self._get_subscription(
            product.wb_mailing_list_id, self.partner.email)
        self.assertTrue(sub, "Active-Lizenz soll Partner subscriben.")
        self.assertFalse(sub.opt_out)

    def test_subscribe_idempotent(self):
        product = self._make_product(code='IDEM')
        license = self._make_license(product, state='active')
        # state-write von 'active' auf 'active' triggert nichts,
        # aber expliziter Methoden-Aufruf darf keine Doublette erzeugen
        license._wb_subscribe_to_mailing_list()
        license._wb_subscribe_to_mailing_list()
        Subscription = self.env['mailing.subscription'].sudo()
        Contact = self.env['mailing.contact'].sudo()
        contact = Contact.search([('email', '=ilike', self.partner.email)], limit=1)
        count = Subscription.search_count([
            ('contact_id', '=', contact.id),
            ('list_id', '=', product.wb_mailing_list_id.id),
        ])
        self.assertEqual(count, 1,
            "Doppelter Subscribe-Aufruf darf keine doppelte Subscription anlegen.")

    # ------------------------------------------------ Unsubscribe bei expired

    def test_state_expired_unsubscribes(self):
        product = self._make_product(code='EXP1')
        license = self._make_license(product, state='active')
        license._wb_subscribe_to_mailing_list()
        sub = self._get_subscription(
            product.wb_mailing_list_id, self.partner.email)
        self.assertTrue(sub and not sub.opt_out)
        license.write({'state': 'expired'})
        sub.invalidate_recordset()
        self.assertTrue(sub.exists(),
            "Subscription darf nicht geloescht werden — DSGVO-Audit-Trail.")
        self.assertTrue(sub.opt_out,
            "Bei expired soll opt_out=True gesetzt werden.")

    def test_state_revoked_unsubscribes(self):
        product = self._make_product(code='REV1')
        license = self._make_license(product, state='active')
        license._wb_subscribe_to_mailing_list()
        license.write({'state': 'revoked'})
        sub = self._get_subscription(
            product.wb_mailing_list_id, self.partner.email)
        self.assertTrue(sub.opt_out)

    def test_state_grace_keeps_subscription(self):
        product = self._make_product(code='GRC1')
        license = self._make_license(product, state='active')
        license._wb_subscribe_to_mailing_list()
        license.write({'state': 'grace'})
        sub = self._get_subscription(
            product.wb_mailing_list_id, self.partner.email)
        self.assertFalse(sub.opt_out,
            "Grace-Lizenzen sollen Service-Mails weiter bekommen.")

    # --------------------------------------- Re-Subscribe nach opt_out

    def test_resubscribe_after_optout(self):
        product = self._make_product(code='REOP')
        license = self._make_license(product, state='active')
        license._wb_subscribe_to_mailing_list()
        license.write({'state': 'expired'})
        sub = self._get_subscription(
            product.wb_mailing_list_id, self.partner.email)
        self.assertTrue(sub.opt_out)
        # Renewal-Szenario: License wird wieder aktiviert
        license.write({
            'state': 'active',
            'activated_at': fields.Datetime.now(),
        })
        sub.invalidate_recordset()
        self.assertFalse(sub.opt_out,
            "Re-Subscribe soll opt_out wieder auf False setzen.")

    # ------------------------- Mehrere Lizenzen pro (partner, product)

    def test_unsubscribe_keeps_list_when_other_active_license_exists(self):
        product = self._make_product(code='MULT')
        license_a = self._make_license(product, state='active')
        license_b = self._make_license(product, state='active')
        license_a._wb_subscribe_to_mailing_list()
        # license_a auslaufen lassen — license_b ist noch active
        license_a.write({'state': 'expired'})
        sub = self._get_subscription(
            product.wb_mailing_list_id, self.partner.email)
        self.assertFalse(sub.opt_out,
            "Solange noch eine active/grace-Lizenz fuer (partner, product) "
            "existiert, darf nicht unsubscribed werden.")
