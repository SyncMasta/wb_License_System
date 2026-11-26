"""Migrate-Wizard.

Anwendungsfall: Der Wizard wird auf der NEUEN Instanz geöffnet (wo die
Lizenz hin soll). Liest dort Domain + DB-UUID und sendet sie an den Server
als Migration-Request.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class WbLicenseMigrateWizard(models.TransientModel):
    _name = 'wb.license.migrate.wizard'
    _description = 'License Migration Wizard'

    product_code = fields.Char(size=4, required=True)
    current_domain = fields.Char(string='Current Domain (old)', readonly=True)
    current_db_uuid = fields.Char(string='Current Database ID (old)', readonly=True)
    new_domain = fields.Char(
        string='New Domain',
        required=True,
        default=lambda self: self.env['wb.license.client']._get_domain() or '',
    )
    new_db_uuid = fields.Char(
        string='New Database ID',
        readonly=True,
        default=lambda self: self.env['wb.license.client']._get_db_uuid() or '',
    )
    reason = fields.Text(string='Reason', required=True)
    contact_email = fields.Char(
        string='Contact Email',
        required=True,
        default=lambda self: self.env.user.email or '',
    )

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        product_code = vals.get('product_code')
        if product_code:
            info = self.env['wb.license.info'].search([
                ('product_code', '=', product_code),
                ('company_id', '=', self.env.company.id),
            ], limit=1)
            if info:
                vals['current_domain'] = (
                    info.server_response_raw and 'previous' not in vals
                    and self.env['wb.license.client']._get_domain()
                )
        return vals

    def action_request_migration(self):
        self.ensure_one()
        self.env['wb.license.client'].request_migration(
            self.product_code,
            self.new_domain,
            self.new_db_uuid,
            self.reason,
            self.contact_email,
        )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'title': _("Migration requested"),
                'message': _(
                    "Your migration request has been sent. You will be notified "
                    "as soon as it has been processed (usually 1-2 working days)."
                ),
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
