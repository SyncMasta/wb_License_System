"""Rechnungs-Erweiterung für Lizenz-Zeilen.

Sammelt die WB-License-Keys, die zu den Rechnungszeilen gehören, damit
das QWeb-Rechnungs-Template sie pro Zeile anzeigen kann.

Chain: account.move.line → sale_line_ids → order_id → wb_license_key_ids
"""

from odoo import api, fields, models


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    wb_license_ids = fields.Many2many(
        'wb.license.key',
        compute='_compute_wb_license_ids',
        string='WB-Lizenzen',
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
