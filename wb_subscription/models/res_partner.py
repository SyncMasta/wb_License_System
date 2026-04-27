"""Erweiterungen auf res.partner für Smart-Buttons auf Kunden-Form."""

from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    wb_license_key_ids = fields.One2many(
        'wb.license.key',
        'partner_id',
        string='WB-Lizenzen',
    )
    wb_license_count = fields.Integer(
        compute='_compute_wb_license_count',
        string='Anzahl Lizenzen',
    )
    wb_active_license_count = fields.Integer(
        compute='_compute_wb_license_count',
        string='Aktive Lizenzen',
    )
    wb_migration_count = fields.Integer(
        compute='_compute_wb_migration_count',
        string='Anzahl Migrationen',
    )
    wb_pending_migration_count = fields.Integer(
        compute='_compute_wb_migration_count',
        string='Pending Migrationen',
    )

    @api.depends('wb_license_key_ids.state')
    def _compute_wb_license_count(self):
        for partner in self:
            partner.wb_license_count = len(partner.wb_license_key_ids)
            partner.wb_active_license_count = len(
                partner.wb_license_key_ids.filtered(lambda l: l.state == 'active')
            )

    def _compute_wb_migration_count(self):
        Migration = self.env['wb.license.migration.request']
        for partner in self:
            migrations = Migration.search([('partner_id', '=', partner.id)])
            partner.wb_migration_count = len(migrations)
            partner.wb_pending_migration_count = len(
                migrations.filtered(lambda m: m.state == 'pending')
            )

    def action_view_wb_licenses(self):
        """Smart-Button-Action: öffnet gefilterte Lizenz-Liste für diesen Partner."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f"Lizenzen von {self.name}",
            'res_model': 'wb.license.key',
            'view_mode': 'list,form',
            'domain': [('partner_id', '=', self.id)],
            'context': {'default_partner_id': self.id},
        }

    def action_view_wb_migrations(self):
        """Smart-Button-Action: öffnet Migrations-Liste für diesen Partner."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f"Migrationen von {self.name}",
            'res_model': 'wb.license.migration.request',
            'view_mode': 'list,form',
            'domain': [('partner_id', '=', self.id)],
        }
