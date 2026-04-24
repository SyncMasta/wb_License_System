"""Trial-Anfrage mit Lead-Capture.

Trials haben:
- KEIN Activation-Code (Friction-Reduction, DECISION #28)
- 7 Tage Laufzeit
- 1 Trial pro Email + 1 pro Domain (Rate-Limit, DECISION #29)
"""

from odoo import _, api, fields, models


TRIAL_STATES = [
    ('pending', 'Pending (wartet auf Auto-Approval)'),
    ('active', 'Active (Trial läuft)'),
    ('converted', 'Converted (zu Paid)'),
    ('expired', 'Expired'),
    ('rejected', 'Rejected (Missbrauchs-Verdacht)'),
]


COMPANY_SIZES = [
    ('solo', 'Solo / Freiberufler'),
    ('2-10', '2–10 Mitarbeiter'),
    ('11-50', '11–50 Mitarbeiter'),
    ('51-250', '51–250 Mitarbeiter'),
    ('251+', '251+ Mitarbeiter'),
]


class WbLicenseTrialRequest(models.Model):
    _name = 'wb.license.trial.request'
    _description = 'Trial-Anfrage mit Lead-Capture'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'

    name = fields.Char(
        required=True,
        readonly=True,
        default='Neu',
        copy=False,
    )
    product_id = fields.Many2one(
        'product.product',
        string='Gewünschtes Produkt',
        required=True,
        domain=[('wb_is_license_product', '=', True)],
    )

    contact_name = fields.Char(string='Name', required=True)
    contact_email = fields.Char(string='Email', required=True, index=True)
    contact_phone = fields.Char(string='Telefon')
    company_name = fields.Char(string='Firma', required=True)
    company_size = fields.Selection(COMPANY_SIZES)
    industry = fields.Char(string='Branche')
    domain = fields.Char(string='Odoo-Domain', required=True, index=True)
    expected_use_case = fields.Text(string='Geplanter Einsatz')

    state = fields.Selection(TRIAL_STATES, required=True, default='pending', tracking=True)
    license_id = fields.Many2one('wb.license.key', string='Erzeugte Trial-Lizenz', readonly=True)
    partner_id = fields.Many2one('res.partner', string='Kontakt (auto-erstellt)')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name') or vals['name'] == 'Neu':
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'wb.license.trial.request') or 'TRL/XXXX'
        return super().create(vals_list)

    def action_approve_and_create_trial(self):
        """Legt res.partner + wb.license.key (state='trial') an und aktiviert das Trial."""
        from datetime import timedelta
        for rec in self:
            if rec.state != 'pending':
                continue

            partner = rec.partner_id
            if not partner:
                partner = self.env['res.partner'].search([
                    ('email', '=ilike', rec.contact_email)
                ], limit=1)
            if not partner:
                partner = self.env['res.partner'].create({
                    'name': rec.contact_name,
                    'email': rec.contact_email,
                    'phone': rec.contact_phone,
                    'company_type': 'company' if rec.company_name else 'person',
                    'is_company': bool(rec.company_name),
                    'comment': _("Auto-erzeugt aus Trial-Request %s") % rec.name,
                })

            license = self.env['wb.license.key'].create({
                'product_id': rec.product_id.id,
                'partner_id': partner.id,
                'state': 'trial',
                'valid_from': fields.Date.today(),
                'valid_to': fields.Date.today() + timedelta(days=7),
                'activated_at': fields.Datetime.now(),
                'bound_domain': rec.domain,
            })
            rec.write({
                'state': 'active',
                'partner_id': partner.id,
                'license_id': license.id,
            })
            self.env['wb.license.event'].log_event(
                license, 'trial_started',
                domain=rec.domain,
                details={'trial_request_id': rec.id},
            )
