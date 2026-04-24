"""Erweiterungen auf sale.subscription für Lizenz-Abos."""

from odoo import api, fields, models


class SaleSubscription(models.Model):
    _inherit = 'sale.subscription'

    wb_license_key_ids = fields.One2many(
        'wb.license.key',
        'subscription_id',
        string='Lizenzschlüssel',
    )
    wb_is_license_sub = fields.Boolean(
        compute='_compute_wb_is_license_sub',
        store=True,
    )
    wb_license_count = fields.Integer(
        compute='_compute_wb_license_count',
        store=False,
    )

    @api.depends('order_line.product_id.wb_is_license_product')
    def _compute_wb_is_license_sub(self):
        for sub in self:
            sub.wb_is_license_sub = any(
                line.product_id.wb_is_license_product for line in sub.order_line
            )

    @api.depends('wb_license_key_ids')
    def _compute_wb_license_count(self):
        for sub in self:
            sub.wb_license_count = len(sub.wb_license_key_ids)
