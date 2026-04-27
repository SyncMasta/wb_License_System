"""Rechnungs-Erweiterung für Lizenz-Zeilen + Payment-Hook für License-Issuance.

Zwei Aufgaben:

1. wb_license_ids-Felder auf account.move(.line) für die QWeb-Rechnung,
   damit pro Zeile Key + Domain + Laufzeit angezeigt werden können.

2. **Payment-Hook**: Wenn payment_state einer Out-Invoice auf 'paid' oder
   'in_payment' wechselt, wird auf den verknüpften sale.order(s)
   `_wb_issue_license_keys` aufgerufen. So wird die Lizenz erst erzeugt
   wenn das Geld da ist — folgt dem Odoo-Standard-Flow ohne Stripe-
   spezifischen Code.
"""

import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


PAID_STATES = ('paid', 'in_payment', 'partial')


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    wb_license_ids = fields.Many2many(
        'wb.license.key',
        compute='_compute_wb_license_ids',
        string='WB-Lizenzen',
        groups='wb_subscription.group_wb_subscription_user',
        help="Lizenzschlüssel, die zu dieser Rechnungszeile gehören. "
             "Wird über die verknüpfte Sale Order → Subscription ermittelt.",
    )

    @api.depends('sale_line_ids.order_id')
    def _compute_wb_license_ids(self):
        for line in self:
            if not line.product_id.wb_is_license_product:
                line.wb_license_ids = self.env['wb.license.key']
                continue
            keys = self.env['wb.license.key']
            for sol in line.sale_line_ids:
                keys |= sol.order_id.wb_license_key_ids.filtered(
                    lambda k: k.product_id == line.product_id
                )
            line.wb_license_ids = keys


class AccountMove(models.Model):
    _inherit = 'account.move'

    wb_license_ids = fields.Many2many(
        'wb.license.key',
        compute='_compute_wb_license_ids',
        string='WB-Lizenzen auf Rechnung',
        groups='wb_subscription.group_wb_subscription_user',
    )
    wb_has_license_lines = fields.Boolean(
        compute='_compute_wb_license_ids',
        store=False,
    )

    @api.depends('invoice_line_ids.wb_license_ids')
    def _compute_wb_license_ids(self):
        for move in self:
            keys = move.invoice_line_ids.mapped('wb_license_ids')
            move.wb_license_ids = keys
            move.wb_has_license_lines = bool(keys)

    def write(self, vals):
        """Override: bei payment_state-Wechsel auf 'paid' werden Lizenzen erzeugt.

        Wir prüfen den state-Übergang vor dem write, um Doppel-Trigger zu
        vermeiden. _wb_issue_license_keys ist selbst idempotent.
        """
        moves_just_paid = self.env['account.move']
        if 'payment_state' in vals and vals['payment_state'] in PAID_STATES:
            moves_just_paid = self.filtered(
                lambda m: m.move_type == 'out_invoice'
                and m.payment_state not in PAID_STATES
                and m.wb_has_license_lines
            )

        result = super().write(vals)

        if moves_just_paid:
            for move in moves_just_paid:
                try:
                    orders = move.invoice_line_ids.mapped('sale_line_ids.order_id')
                    for order in orders:
                        order._wb_issue_license_keys()
                except Exception as e:
                    _logger.exception(
                        "[wb_subscription] License-Issuance auf Rechnung %s fehlgeschlagen: %s",
                        move.name, e)
        return result
