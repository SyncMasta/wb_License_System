"""Persistenter Status-Cache für WB-Lizenzen.

WICHTIG: models.Model, NICHT TransientModel. Der Cache muss Odoo-Restarts
überleben, sonst würde bei jedem Service-Neustart der Banner "Lizenz-Server
unerreichbar" aufpoppen bis der nächste Cron-Ping durchläuft.

is_valid-Logik nach v1.5:
- active / grace      → True
- expired / unlicensed → False
- unknown             → True wenn last_server_check < min_cache_age_days alt,
                        sonst False (default: 30 Tage Toleranz)
"""

import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


STATE_SELECTION = [
    ('active', 'Aktiv'),
    ('grace', 'Grace (Rechnung offen)'),
    ('expired', 'Expired'),
    ('unknown', 'Unbekannt (Server unerreichbar)'),
    ('unlicensed', 'Kein Key konfiguriert'),
]


class WbLicenseInfo(models.Model):
    _name = 'wb.license.info'
    _description = 'WB Lizenz-Status-Cache (persistent)'
    _order = 'product_code'
    _rec_name = 'product_code'

    product_code = fields.Char(
        size=4,
        required=True,
        index=True,
        help="4-stelliger Produkt-Code, z.B. 'TELE'.",
    )
    key = fields.Char(
        string='Lizenzschlüssel',
        help="Public Key (Kopie aus ir.config_parameter für Anzeige).",
    )
    key_masked = fields.Char(
        compute='_compute_key_masked',
        string='Key (maskiert)',
    )
    state = fields.Selection(
        STATE_SELECTION,
        required=True,
        default='unlicensed',
        index=True,
    )
    valid_from = fields.Date()
    valid_to = fields.Date()
    grace_until = fields.Date()
    days_remaining = fields.Integer(
        compute='_compute_days_remaining',
        store=False,
    )

    last_server_check = fields.Datetime(
        string='Letzter erfolgreicher Check',
        help="Zeitpunkt der letzten Antwort vom WB-Server.",
    )
    last_check_success = fields.Boolean(default=False)
    last_error_message = fields.Char()
    server_response_raw = fields.Text(
        string='Raw Server Response (JSON)',
        help="Für Debugging — letzte Rohantwort vom Server.",
    )

    effective_min_cache_age_days = fields.Integer(
        string='Min Cache-Age (Tage)',
        default=30,
        help="Temporär angewandter min_cache_age_days beim letzten check_license-Aufruf. "
             "Methodenspezifisch setzbar via @license_required.",
    )
    is_valid = fields.Boolean(
        compute='_compute_is_valid',
        store=False,
        help="True wenn Feature gated-Methoden laufen dürfen.",
    )
    user_message = fields.Text(
        compute='_compute_user_message',
        store=False,
    )

    company_id = fields.Many2one(
        'res.company',
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )

    _sql_constraints = [
        ('product_company_unique',
         'UNIQUE(product_code, company_id)',
         'Pro Produkt-Code und Company darf es nur einen Cache-Eintrag geben.'),
    ]

    @api.depends('key')
    def _compute_key_masked(self):
        for rec in self:
            if not rec.key or len(rec.key) < 6:
                rec.key_masked = rec.key or ''
                continue
            rec.key_masked = f"{rec.key[:8]}...{rec.key[-2:]}"

    @api.depends('valid_to')
    def _compute_days_remaining(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.days_remaining = (rec.valid_to - today).days if rec.valid_to else 0

    @api.depends('state', 'last_server_check', 'effective_min_cache_age_days')
    def _compute_is_valid(self):
        now = fields.Datetime.now()
        for rec in self:
            if rec.state == 'active':
                rec.is_valid = True
            elif rec.state == 'grace':
                rec.is_valid = True
            elif rec.state in ('expired', 'unlicensed'):
                rec.is_valid = False
            elif rec.state == 'unknown':
                if not rec.last_server_check:
                    rec.is_valid = False
                    continue
                max_age = timedelta(days=rec.effective_min_cache_age_days or 30)
                rec.is_valid = (now - rec.last_server_check) <= max_age
            else:
                rec.is_valid = False

    @api.depends('state', 'grace_until', 'valid_to', 'last_server_check')
    def _compute_user_message(self):
        for rec in self:
            if rec.state == 'active':
                rec.user_message = False
            elif rec.state == 'grace':
                rec.user_message = _(
                    "Ihre Lizenz für %s läuft aus wegen einer überfälligen Rechnung. "
                    "Bitte bezahlen Sie die offene Rechnung bis zum %s, um "
                    "Unterbrechungen zu vermeiden."
                ) % (rec.product_code, rec.grace_until or '-')
            elif rec.state == 'expired':
                rec.user_message = _(
                    "Die Lizenz für %s ist abgelaufen. Bitte kontaktieren Sie "
                    "support@wissen-beratung.de für eine Verlängerung."
                ) % rec.product_code
            elif rec.state == 'unknown':
                rec.user_message = _(
                    "Der Lizenz-Server ist nicht erreichbar. Ihre Lizenz funktioniert "
                    "weiter — bei anhaltenden Problemen bitte Internet-Verbindung prüfen "
                    "oder support@wissen-beratung.de kontaktieren."
                )
            elif rec.state == 'unlicensed':
                rec.user_message = _(
                    "Keine Lizenz für %s konfiguriert. Bitte unter Einstellungen "
                    "→ WISSEN BERATUNG Lizenzen aktivieren."
                ) % rec.product_code
            else:
                rec.user_message = False

    def apply_cache_age_policy(self, min_cache_age_days):
        """Wird von wb.license.client.check_license gerufen, damit is_valid
        den passenden Wert für diesen Aufruf hat. Persistiert als Feld,
        damit das UI die Policy anzeigen kann."""
        for rec in self:
            if rec.effective_min_cache_age_days != min_cache_age_days:
                rec.effective_min_cache_age_days = min_cache_age_days

    def apply_server_response(self, data):
        """Trägt die Antwort eines erfolgreichen Pings in den Cache ein."""
        self.ensure_one()
        import json
        state = data.get('state') or data.get('status')
        vals = {
            'state': state if state in dict(STATE_SELECTION) else 'unknown',
            'valid_from': data.get('valid_from') or False,
            'valid_to': data.get('valid_to') or False,
            'grace_until': data.get('grace_until') or False,
            'last_server_check': fields.Datetime.now(),
            'last_check_success': True,
            'last_error_message': False,
            'server_response_raw': json.dumps(data, ensure_ascii=False),
        }
        old_state = self.state
        self.write(vals)
        if old_state != vals['state']:
            self._notify_state_change(old_state, vals['state'])

    def record_check_failure(self, status, data):
        """Server-Fehler dokumentieren, state nicht überschreiben.

        Beim letzten bekannten Stand bleiben — is_valid entscheidet
        selbst anhand last_server_check-Alter, ob der Cache noch gilt.
        """
        self.ensure_one()
        msg = f"HTTP {status}"
        if data and isinstance(data, dict) and data.get('error'):
            msg = f"HTTP {status}: {data['error']}"
        self.write({
            'last_check_success': False,
            'last_error_message': msg,
        })
        if self.state == 'active':
            self.state = 'unknown'

    def _notify_state_change(self, old_state, new_state):
        """Bei kritischem Übergang Admin-Mail senden."""
        critical_transitions = {
            ('active', 'grace'): 'wb_license_client.mail_template_grace_started_local',
            ('active', 'expired'): 'wb_license_client.mail_template_expired_local',
            ('grace', 'expired'): 'wb_license_client.mail_template_expired_local',
            ('unknown', 'active'): None,
            ('expired', 'active'): None,
        }
        template_xmlid = critical_transitions.get((old_state, new_state))
        if not template_xmlid:
            return
        template = self.env.ref(template_xmlid, raise_if_not_found=False)
        if not template:
            _logger.warning(
                "[wb_license_client] Mail-Template %s nicht gefunden", template_xmlid)
            return
        try:
            template.send_mail(self.id, force_send=False)
        except Exception as e:
            _logger.exception(
                "[wb_license_client] Admin-Mail-Versand fehlgeschlagen: %s", e)

    def action_open_activate_wizard(self):
        """Smart-Button-Action: öffnet Activate-Wizard für diesen Eintrag."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Lizenz aktivieren"),
            'res_model': 'wb.license.activate.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_product_code': self.product_code},
        }

    def action_force_ping(self):
        """Admin-Action: erzwingt einen Server-Ping außerhalb des normalen Cron."""
        self.ensure_one()
        key = self.env['wb.license.client']._get_stored_key(self.product_code)
        if not key:
            raise UserError(_("Kein Key gespeichert für %s.") % self.product_code)
        self.env['wb.license.client']._do_ping(self, self.product_code, key)
