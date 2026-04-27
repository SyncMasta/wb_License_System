"""sale.order Erweiterungen + License-Issuance-Logik.

Lizenz-Erzeugung läuft NICHT auf sale.order._action_confirm, sondern erst
wenn die zugehörige Rechnung als bezahlt markiert ist — siehe
account_move.py. So folgt der Bestell-/Zahlungs-Flow dem Odoo-Standard:

  1. Bestellung wird angelegt (Webseite POST oder manuell)
  2. Tobias bestätigt sale.order → Rechnung-Draft entsteht
  3. Rechnung wird versendet (mit Standard-Payment-Link)
  4. Kunde zahlt über den Link (Stripe / SEPA / was auch immer in Odoo
     als Payment-Provider konfiguriert ist)
  5. Zahlungseingang setzt account.move.payment_state = 'paid'
  6. account.move.write hook → _wb_issue_license_keys auf der zugehörigen Order

Damit ist wb_subscription frei von Stripe-spezifischem Code (DECISION #7:
Odoo-Standards nutzen). Tobias kann Provider wechseln ohne dieses Modul anzufassen.

Klartext-Activation-Code lebt nur im RAM zwischen Generate und Mail-Versand,
wird danach mit `del` verworfen. In der DB steht nur der bcrypt-Hash + der
Fernet-encrypted Code im Ticket (siehe DECISION #48).
"""

import logging
from datetime import date, timedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    wb_license_key_ids = fields.One2many(
        'wb.license.key',
        'sale_order_id',
        string='WB-Lizenzschlüssel',
    )
    wb_is_license_sub = fields.Boolean(
        compute='_compute_wb_is_license_sub',
        store=True,
        help="True wenn ≥1 Order-Line ein Lizenz-Produkt referenziert.",
    )
    wb_license_count = fields.Integer(
        compute='_compute_wb_license_count',
        string='Anzahl Lizenzen',
    )

    @api.depends('order_line.product_id.wb_is_license_product')
    def _compute_wb_is_license_sub(self):
        for order in self:
            order.wb_is_license_sub = any(
                line.product_id.wb_is_license_product for line in order.order_line
            )

    @api.depends('wb_license_key_ids')
    def _compute_wb_license_count(self):
        for order in self:
            order.wb_license_count = len(order.wb_license_key_ids)

    def _wb_compute_valid_to(self):
        """Anteilige Erstlaufzeit bis 31.12. (DECISION #4).

        Wenn schon im Dezember bestellt → bis 31.12. nächstes Jahr.
        """
        today = fields.Date.context_today(self)
        eoy = date(today.year, 12, 31)
        if (eoy - today).days < 30:
            eoy = date(today.year + 1, 12, 31)
        return eoy

    def _wb_issue_license_keys(self):
        """Erzeugt für jede unbearbeitete Lizenz-Zeile einen wb.license.key.

        Idempotent: Wenn für (order_line, product) bereits ein Key existiert,
        wird übersprungen — Mehrfach-Aufruf bei mehreren Zahlungseingängen
        oder Webhook-Replays ist sicher.

        Wird ausgelöst durch account.move.write wenn payment_state auf
        'paid' oder 'in_payment' wechselt — siehe account_move.py.
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
                ('valid_from', '>=', fields.Date.context_today(self) - timedelta(days=7)),
            ], limit=1)
            if existing:
                _logger.info(
                    "[wb_subscription] Lizenz für Order %s / Produkt %s existiert "
                    "bereits (%s) — skip Erzeugung",
                    self.name, line.product_id.display_name, existing.name)
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
                'sale_order_id': self.id,
                'state': 'issued',
                'valid_from': valid_from,
                'valid_to': valid_to,
                'instance_limit': instance_limit,
                'activation_hash': activation_hash,
                'activation_expires_at': fields.Datetime.now() + timedelta(days=grace_days),
                'company_id': self.company_id.id,
            })

            Ticket.create({
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

    @api.model
    def _cron_generate_renewal_invoices(self):
        """Cron 01.12., 06:00 UTC — generiert Draft-Renewal-Rechnungen.

        Pro sale.order mit is_subscription=True und wb_is_license_sub=True
        wird ein account.move (Draft) erstellt für die nächste Periode.
        Tobias gibt die Drafts dann manuell frei (DECISION #30).

        Idempotent: Wenn schon eine Draft-Rechnung im Zieljahr für diese
        Order existiert, wird keine neue erzeugt.

        Nutzt Odoo 19 EE Standard: sale.order ist die Subscription
        (kein separates sale.subscription-Modell mehr).
        """
        from datetime import date

        today = fields.Date.today()
        target_year = today.year + 1 if today.month == 12 else today.year

        domain = [('wb_is_license_sub', '=', True), ('state', '=', 'sale')]
        if 'is_subscription' in self._fields:
            domain.append(('is_subscription', '=', True))
        if 'subscription_state' in self._fields:
            domain.append(('subscription_state', 'in', ['3_progress', '4_paused']))

        orders = self.search(domain)
        created = 0
        for order in orders:
            existing = self.env['account.move'].sudo().search([
                ('move_type', '=', 'out_invoice'),
                ('state', '=', 'draft'),
                ('partner_id', '=', order.partner_id.id),
                ('invoice_date', '>=', date(target_year, 1, 1)),
                ('invoice_date', '<=', date(target_year, 12, 31)),
                ('invoice_line_ids.product_id', 'in', order.order_line.mapped('product_id').ids),
            ], limit=1)
            if existing:
                continue
            try:
                move = self.env['account.move'].sudo().create({
                    'move_type': 'out_invoice',
                    'partner_id': order.partner_id.id,
                    'invoice_date': date(target_year, 1, 1),
                    'invoice_line_ids': [
                        (0, 0, {
                            'product_id': line.product_id.id,
                            'quantity': line.product_uom_qty,
                            'name': f"{line.product_id.name} — Renewal {target_year}",
                            'price_unit': line.price_unit,
                        })
                        for line in order.order_line
                        if line.product_id.wb_is_license_product
                    ],
                })
                if move.invoice_line_ids:
                    created += 1
            except Exception as e:
                _logger.exception(
                    "[wb_subscription] Renewal-Invoice-Generation für %s fehlgeschlagen: %s",
                    order.name, e)

        _logger.info(
            "[wb_subscription] Dezember-Renewal-Cron: %d Draft-Rechnungen erzeugt für %d",
            created, target_year)
        if created > 0:
            self.env['wb.telegram.notifier'].send_message(
                f"📋 Dezember-Renewal: {created} Draft-Rechnungen für {target_year} "
                f"erzeugt — bitte unter Buchhaltung prüfen und freigeben."
            )
