"""Migration-Requests für Domain-/Instanz-Umzüge.

Siehe ARCHITECTURE.md 9.4 und DECISIONS.md #22:
- 1x pro Jahr erlaubt
- Tobias-Approval-Workflow
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError


MIGRATION_STATES = [
    ('pending', 'Pending (wartet auf Review)'),
    ('approved', 'Approved'),
    ('rejected', 'Rejected'),
    ('completed', 'Completed'),
]


class WbLicenseMigrationRequest(models.Model):
    _name = 'wb.license.migration.request'
    _description = 'Migrationsantrag für Lizenz-Umzug'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'

    name = fields.Char(
        required=True,
        readonly=True,
        default='Neu',
        copy=False,
    )
    license_id = fields.Many2one(
        'wb.license.key',
        required=True,
        ondelete='cascade',
        tracking=True,
    )
    partner_id = fields.Many2one(
        related='license_id.partner_id',
        store=True,
        readonly=True,
    )

    current_domain = fields.Char(string='Aktuelle Domain', readonly=True)
    current_db_uuid = fields.Char(string='Aktuelle DB-UUID', readonly=True)
    new_domain = fields.Char(string='Neue Domain', required=True, tracking=True)
    new_db_uuid = fields.Char(string='Neue DB-UUID', required=True)
    new_fingerprint = fields.Char(compute='_compute_new_fingerprint', store=True)

    reason = fields.Text(string='Begründung', required=True)
    contact_email = fields.Char(string='Kontakt-Email', required=True)

    state = fields.Selection(MIGRATION_STATES, required=True, default='pending', tracking=True)
    reviewed_by_id = fields.Many2one('res.users', readonly=True)
    review_note = fields.Text()
    reviewed_at = fields.Datetime(readonly=True)

    @api.depends('new_domain', 'new_db_uuid')
    def _compute_new_fingerprint(self):
        gen = self.env['wb.key.generator']
        for rec in self:
            if rec.new_domain and rec.new_db_uuid:
                rec.new_fingerprint = gen.compute_fingerprint(rec.new_domain, rec.new_db_uuid)
            else:
                rec.new_fingerprint = False

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name') or vals['name'] == 'Neu':
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'wb.license.migration.request') or 'MIG/XXXX'
        records = super().create(vals_list)
        for rec in records:
            self.env['wb.license.event'].log_event(
                rec.license_id, 'migration_requested',
                details={
                    'migration_id': rec.id,
                    'new_domain': rec.new_domain,
                },
            )
        return records

    def action_approve(self):
        """Ausgelöst durch Tobias. Updated die Lizenz auf die neue Domain/DB-UUID."""
        for rec in self:
            if rec.state != 'pending':
                raise UserError(_("Nur pending-Anträge können genehmigt werden."))
            rec.license_id.write({
                'bound_domain': rec.new_domain,
                'bound_db_uuid': rec.new_db_uuid,
                'activated_fingerprint': rec.new_fingerprint,
            })
            rec.write({
                'state': 'approved',
                'reviewed_by_id': self.env.user.id,
                'reviewed_at': fields.Datetime.now(),
            })
            self.env['wb.license.event'].log_event(
                rec.license_id, 'migration_approved',
                details={'migration_id': rec.id, 'new_domain': rec.new_domain},
            )
        return True

    def action_reject(self):
        for rec in self:
            if rec.state != 'pending':
                raise UserError(_("Nur pending-Anträge können abgelehnt werden."))
            rec.write({
                'state': 'rejected',
                'reviewed_by_id': self.env.user.id,
                'reviewed_at': fields.Datetime.now(),
            })
            self.env['wb.license.event'].log_event(
                rec.license_id, 'migration_rejected',
                details={'migration_id': rec.id, 'note': rec.review_note or ''},
            )
        return True
