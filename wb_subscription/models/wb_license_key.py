"""Hauptmodell der Lizenz-Plattform.

Enthält Public Key, bcrypt-Hash des Activation-Codes, Instanz-Bindung,
Laufzeiten und State-Machine. Siehe ARCHITECTURE.md Kapitel 5.4.

Sicherheits-Kritisch:
- Activation-Code wird NUR als bcrypt-Hash gespeichert (activation_hash).
- Nach erfolgreicher Activation wird activation_hash auf False gesetzt ("burned").
- Activation-Code darf NIEMALS geloggt werden.
"""

import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


STATE_SELECTION = [
    ('issued', 'Issued (Key erstellt, nicht aktiviert)'),
    ('trial', 'Trial (7 Tage)'),
    ('active', 'Aktiv'),
    ('grace', 'Grace (Rechnung überfällig)'),
    ('expired', 'Expired'),
    ('revoked', 'Revoked (gesperrt)'),
    ('cancelled', 'Cancelled'),
]


class WbLicenseKey(models.Model):
    _name = 'wb.license.key'
    _description = 'WB Lizenzschlüssel'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc, id desc'
    _rec_name = 'display_name'

    name = fields.Char(
        string='Public Key',
        required=True,
        copy=False,
        index=True,
        tracking=True,
        help="Lizenzschlüssel im Format WB-{PROD}-{UUID8}{CHK}. Read-only nach Erzeugung.",
    )
    display_name = fields.Char(compute='_compute_display_name', store=True)

    activation_hash = fields.Binary(
        string='Activation-Hash (bcrypt)',
        attachment=False,
        help="bcrypt-Hash des Activation-Codes. Klartext wird nirgendwo gespeichert. "
             "Wird nach erfolgreicher Activation auf False gesetzt.",
        groups='wb_subscription.group_wb_subscription_manager',
    )
    activation_hash_method = fields.Char(default='bcrypt', required=True)
    activated_at = fields.Datetime(string='Aktiviert am', tracking=True)
    activated_fingerprint = fields.Char(
        string='Fingerprint (SHA256)',
        help="Wird beim Activate gesetzt — SHA256(domain + db_uuid).",
    )
    activation_expires_at = fields.Datetime(
        string='Activation-Code läuft ab',
        help="Code kann bis zu diesem Datum verwendet werden.",
    )

    product_id = fields.Many2one(
        'product.product',
        string='Produkt',
        required=True,
        domain=[('wb_is_license_product', '=', True)],
        tracking=True,
    )
    product_code = fields.Char(
        related='product_id.wb_technical_code',
        store=True,
        index=True,
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Kunde',
        required=True,
        tracking=True,
    )
    subscription_id = fields.Many2one(
        'sale.subscription',
        string='Abo',
        ondelete='set null',
    )

    state = fields.Selection(
        STATE_SELECTION,
        required=True,
        default='issued',
        tracking=True,
        index=True,
    )

    valid_from = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    valid_to = fields.Date(required=True, tracking=True)
    grace_until = fields.Date(
        compute='_compute_grace_until',
        store=True,
        help="Abgeleitet aus partner.followup_status. Nur bei state='grace' relevant.",
    )
    last_renewal_date = fields.Date()

    bound_domain = fields.Char(string='Gebundene Domain', tracking=True)
    bound_db_uuid = fields.Char(string='Gebundene DB-UUID')
    instance_limit = fields.Integer(default=1, required=True)
    instance_current = fields.Integer(compute='_compute_instance_current', store=False)

    last_seen_at = fields.Datetime()
    last_seen_ip = fields.Char()
    last_seen_user_agent = fields.Char()
    ping_count_total = fields.Integer(default=0)

    event_ids = fields.One2many('wb.license.event', 'license_id', string='Events', readonly=True)
    migration_ids = fields.One2many('wb.license.migration.request', 'license_id', string='Migrationen')
    ticket_ids = fields.One2many('wb.activation.ticket', 'license_id', string='Tickets')

    tag_ids = fields.Many2many(
        'wb.license.tag',
        'wb_license_key_tag_rel',
        'license_id',
        'tag_id',
        string='Tags',
    )
    note = fields.Text(string='Notiz (kunden-sichtbar)')
    internal_note = fields.Text(string='Interne Notiz')

    company_id = fields.Many2one(
        'res.company',
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    currency_id = fields.Many2one(related='company_id.currency_id', readonly=True)

    _sql_constraints = [
        ('name_unique', 'UNIQUE(name)', 'Lizenzschlüssel muss eindeutig sein.'),
        ('instance_limit_positive', 'CHECK(instance_limit >= 1)',
         'instance_limit muss mindestens 1 sein.'),
    ]

    @api.depends('name', 'product_id', 'partner_id')
    def _compute_display_name(self):
        for rec in self:
            parts = [rec.product_id.name or 'Unknown', rec.partner_id.name or '', f"({rec.name})" if rec.name else '']
            rec.display_name = ' — '.join(p for p in parts if p).strip()

    @api.depends('partner_id', 'partner_id.followup_status', 'state')
    def _compute_grace_until(self):
        """Leitet grace_until aus Odoo account_followup-Daten ab.

        Siehe ARCHITECTURE.md 8.1: wenn letzte Mahnstufe erreicht ist,
        startet die Grace-Period, und grace_until = heute + delay aus
        aktueller followup_line.
        """
        for rec in self:
            partner = rec.partner_id
            if not partner or rec.state != 'grace':
                rec.grace_until = False
                continue
            followup_line = getattr(partner, 'followup_line_id', False)
            if followup_line and getattr(followup_line, 'delay', False):
                rec.grace_until = fields.Date.today() + timedelta(days=followup_line.delay)
            else:
                rec.grace_until = fields.Date.today() + timedelta(days=7)

    def _compute_instance_current(self):
        """Zählt distinct bound_db_uuid+domain-Kombinationen aus Events.

        Aktuell einfach: 1 wenn aktiviert, sonst 0. Kann später erweitert
        werden wenn Multi-Instance-Licenses implementiert werden.
        """
        for rec in self:
            rec.instance_current = 1 if rec.activated_at and rec.bound_domain else 0

    @api.constrains('state', 'activated_at')
    def _check_state_activation(self):
        for rec in self:
            if rec.state in ('active', 'grace', 'expired') and not rec.activated_at:
                raise ValidationError(_(
                    "Lizenz %s ist im State '%s', hat aber kein activated_at. "
                    "Aktivierung muss vor State-Wechsel erfolgen."
                ) % (rec.name, rec.state))

    @api.constrains('valid_from', 'valid_to')
    def _check_validity_range(self):
        for rec in self:
            if rec.valid_from and rec.valid_to and rec.valid_to < rec.valid_from:
                raise ValidationError(_(
                    "valid_to (%s) darf nicht vor valid_from (%s) liegen."
                ) % (rec.valid_to, rec.valid_from))

    @api.model_create_multi
    def create(self, vals_list):
        """Generiert Public Key und Activation-Code, wenn nicht explizit angegeben.

        Der Activation-Code wird NUR im RAM gehalten, als bcrypt gehasht
        und dann verworfen. Für die spätere Portal-Anzeige muss er beim
        Caller (sale.subscription._wb_issue_license_keys) parallel an das
        Ticket übergeben werden (Fernet-encrypted).
        """
        for vals in vals_list:
            if not vals.get('name'):
                product = self.env['product.product'].browse(vals.get('product_id'))
                code = product.wb_technical_code
                if not code:
                    raise ValidationError(_(
                        "Produkt '%s' hat keinen wb_technical_code gesetzt."
                    ) % product.display_name)
                vals['name'] = self.env['wb.key.generator'].generate_public_key(code)
            if vals.get('state') in ('issued',) and not vals.get('activation_expires_at'):
                product = self.env['product.product'].browse(vals.get('product_id'))
                grace_days = getattr(product, 'wb_activation_grace_days', 90) or 90
                vals['activation_expires_at'] = fields.Datetime.now() + timedelta(days=grace_days)

        records = super().create(vals_list)
        for rec in records:
            self.env['wb.license.event'].log_event(rec, 'key_generated')
        return records

    def action_revoke(self, reason=None):
        """Manuell sperren. Audit-Event wird geloggt."""
        self.ensure_one()
        self.write({'state': 'revoked'})
        self.env['wb.license.event'].log_event(
            self, 'revoked',
            details={'reason': reason or '(kein Grund angegeben)'},
        )
        return True

    def action_reactivate(self):
        """Von revoked oder expired zurück auf active."""
        self.ensure_one()
        if self.state not in ('revoked', 'expired'):
            raise UserError(_(
                "Reactivate nur möglich wenn Lizenz im State 'revoked' oder 'expired' ist."
            ))
        if not self.activated_at:
            raise UserError(_(
                "Lizenz wurde nie aktiviert — bitte Kunde neu aktivieren lassen."
            ))
        self.write({'state': 'active'})
        self.env['wb.license.event'].log_event(self, 'reactivated')
        return True

    def action_renew(self, new_valid_to):
        """Verlängert Laufzeit auf new_valid_to. Loggt renewal-Event."""
        self.ensure_one()
        if not new_valid_to:
            raise UserError(_("Neues valid_to erforderlich."))
        self.write({
            'valid_to': new_valid_to,
            'last_renewal_date': fields.Date.today(),
            'state': 'active' if self.state in ('grace', 'expired') and self.activated_at else self.state,
        })
        self.env['wb.license.event'].log_event(
            self, 'renewal',
            details={'new_valid_to': str(new_valid_to)},
        )
        return True

    def activate_with_code(self, activation_code, domain, db_uuid, ip=None, user_agent=None):
        """Prüft Activation-Code und aktiviert die Lizenz.

        Wird vom Controller /api/license/activate aufgerufen. Siehe
        ARCHITECTURE.md 6.2 für den kompletten Flow.

        Returns:
            dict mit 'status' und ggf. 'error' (für Controller-Response).
        """
        self.ensure_one()
        gen = self.env['wb.key.generator']

        if self.activated_at:
            self.env['wb.license.event'].log_event(
                self, 'activation_failed',
                ip_address=ip, user_agent=user_agent, domain=domain, db_uuid=db_uuid,
                details={'reason': 'already_activated'},
            )
            return {'status': 'error', 'error': 'ALREADY_ACTIVATED',
                    'bound_to': self.bound_domain}

        if self.activation_expires_at and self.activation_expires_at < fields.Datetime.now():
            self.env['wb.license.event'].log_event(
                self, 'activation_failed',
                ip_address=ip, user_agent=user_agent,
                details={'reason': 'code_expired'},
            )
            return {'status': 'error', 'error': 'ACTIVATION_EXPIRED'}

        if not gen.verify_activation_code(activation_code, self.activation_hash):
            self.env['wb.license.event'].log_event(
                self, 'activation_failed',
                ip_address=ip, user_agent=user_agent, domain=domain, db_uuid=db_uuid,
                details={'reason': 'wrong_code'},
            )
            return {'status': 'error', 'error': 'WRONG_CODE'}

        fingerprint = gen.compute_fingerprint(domain, db_uuid)
        self.write({
            'bound_domain': domain,
            'bound_db_uuid': db_uuid,
            'activated_at': fields.Datetime.now(),
            'activated_fingerprint': fingerprint,
            'activation_hash': False,
            'state': 'active' if self.state == 'issued' else self.state,
        })
        self.env['wb.license.event'].log_event(
            self, 'activation',
            ip_address=ip, user_agent=user_agent,
            domain=domain, db_uuid=db_uuid,
        )
        return {'status': 'ok'}

    def record_ping(self, ip=None, user_agent=None):
        """Trägt Ping-Metadaten ein. Wird vom Check-Endpoint aufgerufen."""
        self.ensure_one()
        self.write({
            'last_seen_at': fields.Datetime.now(),
            'last_seen_ip': ip,
            'last_seen_user_agent': user_agent,
            'ping_count_total': self.ping_count_total + 1,
        })
