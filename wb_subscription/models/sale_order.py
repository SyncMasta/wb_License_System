"""Payment-Hook: sale.order → wb.license.key + wb.activation.ticket + Mails.

Wird in `_action_confirm` aufgerufen, sobald die Bestellung bestätigt
ist (typisch nach Stripe-Payment-Webhook). Die Lizenz wird im State 'issued'
angelegt; der Activation-Code wird Fernet-encrypted im Ticket abgelegt
(siehe DECISION #48). Klartext verbleibt nur kurz im RAM, um die zwei
Mails zu versenden, und wird danach verworfen.
"""

import logging
from datetime import date, timedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    wb_license_key_ids = fields.One2many(
        'wb.license.key',
        compute='_compute_wb_license_key_ids',
        string='WB-Lizenzschlüssel',
    )

    @api.depends('order_line.product_id')
    def _compute_wb_license_key_ids(self):
        Key = self.env['wb.license.key']
        for order in self:
            order.wb_license_key_ids = Key.search([
                ('subscription_id', 'in', order.subscription_ids.ids
                 if 'subscription_ids' in order._fields else [order.id]),
            ])

    def _wb_compute_valid_to(self):
        """Anteilige Erstlaufzeit bis 31.12. (DECISION #4).

        Wenn schon im Dezember bestellt → bis 31.12. nächstes Jahr.
        """
        today = fields.Date.context_today(self)
        eoy = date(today.year, 12, 31)
        if (eoy - today).days < 30:
            eoy = date(today.year + 1, 12, 31)
        return eoy

    def _action_confirm(self):
        """Override: nach Standard-Confirm wird Lizenz erzeugt für Lizenz-Lines."""
        result = super()._action_confirm()
        for order in self:
            order._wb_issue_license_keys()
        return result

    def _wb_issue_license_keys(self):
        """Erzeugt für jede Lizenz-Zeile genau einen wb.license.key.

        Idempotent: wenn schon ein Key für (order, product) existiert,
        wird kein neuer angelegt.
        """
        self.ensure_one()
        Key = self.env['wb.license.key'].sudo()
        Ticket = self.env['wb.activation.ticket'].sudo()
        gen = self.env['wb.key.generator'].sudo()

        for line in self.order_line:
            if not line.product_id.wb_is_license_product:
                continue
            existing = Key.search([
                ('partner_id', '=', self.partner_id.id),
                ('product_id', '=', line.product_id.id),
                ('subscription_id.order_id', '=', self.id) if 'subscription_id' in Key._fields else ('partner_id', '=', self.partner_id.id),
            ], limit=1)
            if existing:
                continue

            valid_from = fields.Date.context_today(self)
            valid_to = self._wb_compute_valid_to()
            instance_limit = line.product_id.wb_instance_limit or 1
            grace_days = line.product_id.wb_activation_grace_days or 90

            activation_code = gen.generate_activation_code()
            activation_hash = gen.hash_activation_code(activation_code)

            key = Key.create({
                'product_id': line.product_id.id,
                'partner_id': self.partner_id.id,
                'subscription_id': self._wb_get_subscription_id(),
                'state': 'issued',
                'valid_from': valid_from,
                'valid_to': valid_to,
                'instance_limit': instance_limit,
                'activation_hash': activation_hash,
                'activation_expires_at': fields.Datetime.now() + timedelta(days=grace_days),
                'company_id': self.company_id.id,
            })

            ticket = Ticket.create({
                'license_id': key.id,
                'email': self.partner_id.email or '',
                'encrypted_code': gen.encrypt_code(activation_code),
            })

            try:
                key.action_generate_certificate_pdf()
            except Exception as e:
                _logger.exception(
                    "[wb_subscription] Cert-Auto-Generate für %s fehlgeschlagen: %s",
                    key.name, e)

            cert_attachments = key.certificate_ids.ids
            key._send_template(
                'wb_subscription.mail_template_payment_received',
                attachments=cert_attachments,
            )
            key._send_template('wb_subscription.mail_template_activation_instructions')
            key._send_telegram(
                "💰 Neuer Kauf: {partner} — {product} ({key})",
                partner=self.partner_id.name or '',
                product=line.product_id.name,
                key=key.name,
            )

            del activation_code

    def _wb_get_subscription_id(self):
        """Findet die Subscription die zu dieser Order gehört.

        In Odoo 19 EE: sale.order kann gleichzeitig sale.subscription sein
        (Single-Inheritance). Falls nicht: subscription_ids M2M.
        """
        self.ensure_one()
        if 'is_subscription' in self._fields and self.is_subscription:
            return self.id
        if 'subscription_ids' in self._fields and self.subscription_ids:
            return self.subscription_ids[0].id
        return False

    @api.model
    def _cron_generate_renewal_invoices(self):
        """Cron 01.12., 06:00 UTC — generiert Draft-Renewal-Rechnungen.

        Pro aktiver Subscription mit auto_renew=True und wb_is_license_sub
        wird ein account.move (Draft) erstellt für die nächste Periode.
        Tobias gibt die Drafts dann manuell frei (DECISION #30).

        Idempotent: Wenn schon eine Draft-Rechnung für die gleiche
        Subscription + Periode existiert, wird keine neue erzeugt.
        """
        from datetime import date

        Subscription = self.env['sale.subscription'] if 'sale.subscription' in self.env else None
        if not Subscription:
            _logger.info(
                "[wb_subscription] sale.subscription Model nicht verfügbar — "
                "Dezember-Renewal-Cron übersprungen.")
            return

        today = fields.Date.today()
        target_year = today.year + 1 if today.month == 12 else today.year

        domain = [('wb_is_license_sub', '=', True)]
        if 'auto_renew' in Subscription._fields:
            domain.append(('auto_renew', '=', True))
        if 'state' in Subscription._fields:
            domain.append(('state', 'in', ['open', 'progress', 'pending']))

        subs = self.env['sale.subscription'].sudo().search(domain)
        created = 0
        for sub in subs:
            existing = self.env['account.move'].sudo().search([
                ('move_type', '=', 'out_invoice'),
                ('state', '=', 'draft'),
                ('partner_id', '=', sub.partner_id.id),
                ('invoice_date', '>=', date(target_year, 1, 1)),
                ('invoice_date', '<=', date(target_year, 12, 31)),
                ('invoice_line_ids.product_id', 'in', sub.order_line.mapped('product_id').ids),
            ], limit=1)
            if existing:
                continue
            try:
                move = self.env['account.move'].sudo().create({
                    'move_type': 'out_invoice',
                    'partner_id': sub.partner_id.id,
                    'invoice_date': date(target_year, 1, 1),
                    'invoice_line_ids': [
                        (0, 0, {
                            'product_id': line.product_id.id,
                            'quantity': line.product_uom_qty,
                            'name': f"{line.product_id.name} — Renewal {target_year}",
                            'price_unit': line.price_unit,
                        })
                        for line in sub.order_line
                        if line.product_id.wb_is_license_product
                    ],
                })
                if move.invoice_line_ids:
                    created += 1
            except Exception as e:
                _logger.exception(
                    "[wb_subscription] Renewal-Invoice-Generation für %s fehlgeschlagen: %s",
                    sub.name, e)

        _logger.info(
            "[wb_subscription] Dezember-Renewal-Cron: %d Draft-Rechnungen erzeugt für %d",
            created, target_year)
        if created > 0:
            self.env['wb.telegram.notifier'].send_message(
                f"📋 Dezember-Renewal: {created} Draft-Rechnungen für {target_year} "
                f"erzeugt — bitte unter Buchhaltung prüfen und freigeben."
            )
