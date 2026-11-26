"""Persistenter Status-Cache für WB-Lizenzen.

WICHTIG: models.Model, NICHT TransientModel. Der Cache muss Odoo-Restarts
überleben, sonst würde bei jedem Service-Neustart der Banner "Lizenz-Server
unerreichbar" aufpoppen bis der nächste Cron-Ping durchläuft.

is_valid-Logik nach v1.5:
- active / grace      → True
- expired / unlicensed → False
- unknown             → True wenn last_server_check < min_cache_age_days alt,
                        sonst False (default: 30 Tage Toleranz)

Sprint 5 / L-H1 — Clock-Anomaly-Detection:
- ``last_server_check`` wird beim Schreiben mit dem alten Wert verglichen.
  Liegt der ALTE Wert in der Zukunft (>= now + Toleranz), wurde die Uhr
  zurückgedreht oder ein DB-Snapshot eingespielt → Anomalie protokolliert.
- ``_compute_is_valid`` lehnt einen Cache mit ``last_server_check > now``
  ab, weil die System-Uhr dann manipuliert wurde — sonst koennte der
  Kunde durch Uhr-Backwards den Cache permanent als ``frisch`` halten.
"""

import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

# Toleranz fuer geringfuegige Clock-Drift (NTP-Resync auf Containern,
# Test-Tenant-Boot etc.). Werte > 1h sollten nicht in legitimer Drift
# vorkommen.
CLOCK_DRIFT_TOLERANCE_MINUTES = 60

_logger = logging.getLogger(__name__)


def parse_max_users(data):
    """User limit from a server answer (DECISIONS.md #95, #97).

    :return: ``0`` (unlimited) if the answer has no ``max_users`` field (older
        servers), the limit for a non-negative integer, ``None`` for any other
        value (invalid: the caller keeps the previous value or refuses the answer).
    """
    if not isinstance(data, dict) or 'max_users' not in data:
        return 0
    value = data['max_users']
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


STATE_SELECTION = [
    ('active', 'Active'),
    ('trial', 'Trial'),
    ('grace', 'Grace (invoice overdue)'),
    ('expired', 'Expired'),
    ('unknown', 'Unknown (server unreachable)'),
    ('unlicensed', 'No key configured'),
]

# Status einer fremden Instanz (Test-DB, Kopie, Umzug), vom Server im Feld
# ``instance`` gemeldet. Siehe wb_License_System/DECISIONS.md #80-82.
INSTANCE_STATUS = [
    ('eval', 'Eval (test system)'),
    ('eval_expired', 'Eval expired'),
    ('eval_limit', 'Eval limit reached'),
    ('dev', 'DEV license'),
    ('dev_not_neutralized', 'DEV license, database not neutralized'),
    ('mismatch_grace', 'Unlicensed instance (grace period)'),
    ('mismatch', 'Unlicensed instance'),
]


class WbLicenseInfo(models.Model):
    _name = 'wb.license.info'
    _description = 'WB License Status Cache'
    _order = 'product_code'
    _rec_name = 'product_code'

    product_code = fields.Char(
        size=4,
        required=True,
        index=True,
        help="4-character alphanumeric product code (A-Z/0-9), "
             "e.g. 'TELE' or 'MCP1'.",
    )
    key = fields.Char(
        string='License Key',
        groups='base.group_system',
        help="Public key (copy from the system parameters, for display).",
    )
    key_masked = fields.Char(
        compute='_compute_key_masked',
        string='Key (masked)',
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
    max_users = fields.Integer(
        string='Max. Users',
        default=0,
        help="User limit of the licensed package as reported by the license server. "
             "0 = unlimited.",
    )

    instance_status = fields.Selection(
        INSTANCE_STATUS,
        string='Instance Status',
        help="Empty if this database is the licensed instance. Otherwise the "
             "server reports eval, DEV license or mismatch here.",
    )
    instance_until = fields.Date(string='Instance Status Valid Until')

    last_server_check = fields.Datetime(
        string='Last Successful Check',
        help="Time of the last answer from the license server.",
    )
    last_check_success = fields.Boolean(default=False)
    last_failed_check_at = fields.Datetime(
        string='Last Failed Check',
        readonly=True,
        help="Time of the last failed ping. The license is checked again at "
             "the earliest 15 minutes later; until then the stored state applies.",
    )
    last_error_message = fields.Char()
    last_clock_anomaly_at = fields.Datetime(
        string='Last Clock Anomaly',
        readonly=True,
        help="Time a manipulated system clock was detected (clock turned back "
             "or database snapshot restored). The cache is not trusted until "
             "the next successful server check.",
    )
    server_response_raw = fields.Text(
        string='Raw Server Response (JSON)',
        groups='base.group_system',
        help="For debugging: last raw answer of the server.",
    )
    last_install_announce_at = fields.Datetime(
        string='Last Install Announcement',
        help="Time of the last install announcement to the license server "
             "(at most once a day).",
    )

    module_technical_name = fields.Char(
        string='Product Module (technical)',
        help="Technical name of the Odoo module of this license "
             "(e.g. 'wb_bitwarden_pro'). Used by the daily check to report "
             "the installed version.",
    )
    installed_module_version = fields.Char(
        string='Installed Module Version',
        compute='_compute_installed_module_version',
        store=False,
        help="Currently installed version of the product module.",
    )
    latest_module_version = fields.Char(
        string='Latest Available Version',
        help="Latest release version reported by the license server. "
             "Updated by the daily check.",
    )
    download_url = fields.Char(
        string='Download URL',
        help="Portal URL for the module download reported by the server.",
    )
    update_available = fields.Boolean(
        string='Update Available',
        compute='_compute_update_available',
        store=False,
        help="Set if a newer module version is available.",
    )

    effective_min_cache_age_days = fields.Integer(
        string='Max. Offline Tolerance (days)',
        default=30,
        help="How long the last successful check stays valid while the license "
             "server cannot be reached (applied by the last license check).",
    )
    is_valid = fields.Boolean(
        compute='_compute_is_valid',
        store=False,
        help="Set if licensed features may be used.",
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
            # sudo: key ist nur für Administratoren lesbar, die Maske für alle (#97)
            key = rec.sudo().key
            if not key or len(key) < 6:
                rec.key_masked = key or ''
                continue
            rec.key_masked = f"{key[:8]}...{key[-2:]}"

    @api.depends('valid_to')
    def _compute_days_remaining(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.days_remaining = (rec.valid_to - today).days if rec.valid_to else 0

    @api.depends('module_technical_name')
    def _compute_installed_module_version(self):
        Module = self.env['ir.module.module'].sudo()
        cache = {}
        for rec in self:
            name = rec.module_technical_name
            if not name:
                rec.installed_module_version = False
                continue
            if name not in cache:
                module = Module.search([('name', '=', name)], limit=1)
                cache[name] = module.latest_version or False
            rec.installed_module_version = cache[name]

    @api.depends('latest_module_version', 'installed_module_version')
    def _compute_update_available(self):
        for rec in self:
            latest = (rec.latest_module_version or '').strip()
            installed = (rec.installed_module_version or '').strip()
            rec.update_available = bool(
                latest and installed and latest != installed
            )

    @api.depends('state', 'last_server_check', 'effective_min_cache_age_days',
                 'last_clock_anomaly_at', 'valid_to')
    def _compute_is_valid(self):
        """Berechnet is_valid und blockt bei erkannter Clock-Anomalie.

        Sprint 5 / L-H1: wenn ``last_server_check`` in der Zukunft liegt
        (Uhr zurueckgedreht ODER DB-Snapshot eingespielt), wird der
        Cache hart als ungueltig behandelt — egal welcher state.
        Sonst koennte ein Kunde durch System-Uhr-Manipulation den
        Cache permanent als ``frisch`` halten.
        """
        now = fields.Datetime.now()
        tolerance = timedelta(minutes=CLOCK_DRIFT_TOLERANCE_MINUTES)
        for rec in self:
            if rec.last_server_check and rec.last_server_check > now + tolerance:
                # Future-dated check → System-Uhr zurueckgedreht, kein Trust
                rec.is_valid = False
                continue
            if rec.state == 'active':
                rec.is_valid = True
            elif rec.state == 'trial':
                # Test endet am valid_to, auch wenn der Server nicht erreichbar
                # ist (sonst verlängerte eine gesperrte Verbindung den Test).
                # Ohne Enddatum gilt ein Test nicht (fail-closed, #97).
                rec.is_valid = bool(rec.valid_to) and rec.valid_to >= fields.Date.context_today(rec)
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

    @api.depends('state', 'grace_until', 'valid_to', 'last_server_check',
                 'instance_status', 'instance_until')
    @api.depends_context('lang')
    def _compute_user_message(self):
        for rec in self:
            if rec.instance_status:
                rec.user_message = rec._instance_message()
            elif rec.state == 'active':
                rec.user_message = False
            elif rec.state == 'trial':
                rec.user_message = _(
                    "Free trial of %(code)s until %(until)s. To keep using it, "
                    "order a license from vertrieb@wissen-beratung.de."
                ) % {'code': rec.product_code, 'until': rec.valid_to or '-'}
            elif rec.state == 'grace':
                rec.user_message = _(
                    "Your license for %(code)s is running out because of an overdue "
                    "invoice. Please pay the open invoice by %(until)s to avoid "
                    "interruptions."
                ) % {'code': rec.product_code, 'until': rec.grace_until or '-'}
            elif rec.state == 'expired':
                rec.user_message = _(
                    "The license for %s has expired. Please contact "
                    "support@wissen-beratung.de to renew it."
                ) % rec.product_code
            elif rec.state == 'unknown':
                rec.user_message = _(
                    "The license server cannot be reached. Your license keeps "
                    "working; if the problem persists, please check the internet "
                    "connection or contact support@wissen-beratung.de."
                )
            elif rec.state == 'unlicensed':
                rec.user_message = _(
                    "No license configured for %s. Please activate one or start a "
                    "free trial under Settings → WB Licenses."
                ) % rec.product_code
            else:
                rec.user_message = False

    def _instance_message(self):
        self.ensure_one()
        code = self.product_code
        until = self.instance_until or '-'
        support = 'support@wissen-beratung.de'
        status = self.instance_status
        if status == 'eval':
            return _(
                "Test system: %(code)s runs on this database for evaluation until "
                "%(until)s. After that it needs a DEV license (%(support)s)."
            ) % {'code': code, 'until': until, 'support': support}
        if status == 'eval_expired':
            return _(
                "The evaluation period of %(code)s on this test system has expired. "
                "This database needs a DEV license (%(support)s)."
            ) % {'code': code, 'support': support}
        if status == 'eval_limit':
            return _(
                "%(code)s already runs on as many test systems for evaluation as "
                "possible. This database needs a DEV license (%(support)s)."
            ) % {'code': code, 'support': support}
        if status == 'dev':
            return _(
                "Development or staging system: %(code)s runs with a DEV license "
                "until %(until)s."
            ) % {'code': code, 'until': until}
        if status == 'dev_not_neutralized':
            return _(
                "The DEV license for %s only applies to neutralized databases. "
                "This database is not neutralized."
            ) % code
        message = _(
            "This database is not the licensed instance for %(code)s. If it is a "
            "test system, neutralize it. After a move, please request the "
            "migration (%(support)s)."
        ) % {'code': code, 'support': support}
        if status == 'mismatch_grace':
            return message + ' ' + _("%(code)s keeps running here until %(until)s.") % {
                'code': code, 'until': until}
        return message

    def apply_cache_age_policy(self, min_cache_age_days):
        """Wird von wb.license.client.check_license gerufen, damit is_valid
        den passenden Wert für diesen Aufruf hat. Persistiert als Feld,
        damit das UI die Policy anzeigen kann."""
        for rec in self:
            if rec.effective_min_cache_age_days != min_cache_age_days:
                rec.effective_min_cache_age_days = min_cache_age_days

    def apply_server_response(self, data):
        """Trägt die Antwort eines erfolgreichen Pings in den Cache ein.

        Sprint 5 / L-H1: prueft beim Schreiben, ob der ALTE
        ``last_server_check`` in der Zukunft lag. Wenn ja → Anomalie
        protokolliert (DB-Snapshot rollback oder Uhr-Manipulation).
        """
        self.ensure_one()
        import json
        state = data.get('state') or data.get('status')
        now = fields.Datetime.now()
        tolerance = timedelta(minutes=CLOCK_DRIFT_TOLERANCE_MINUTES)
        anomaly_detected = (
            self.last_server_check
            and self.last_server_check > now + tolerance
        )
        vals = {
            'state': state if state in dict(STATE_SELECTION) else 'unknown',
            'valid_from': data.get('valid_from') or False,
            'valid_to': data.get('valid_to') or False,
            'grace_until': data.get('grace_until') or False,
            'last_server_check': now,
            'last_check_success': True,
            'last_error_message': False,
            'last_failed_check_at': False,
            'server_response_raw': json.dumps(data, ensure_ascii=False),
            'latest_module_version': (data.get('latest_module_version') or '').strip() or False,
            'download_url': (data.get('download_url') or '').strip() or False,
        }
        max_users = parse_max_users(data)
        if max_users is None:
            if not self.last_server_check:
                # Erste Antwort mit ungültiger Grenze: nicht übernehmen (#97, M3)
                self.record_check_failure(200, {'error': 'INVALID_MAX_USERS'})
                return
            max_users = self.max_users
        vals['max_users'] = max_users
        instance = data.get('instance') if isinstance(data.get('instance'), dict) else {}
        status = instance.get('status')
        vals['instance_status'] = status if status in dict(INSTANCE_STATUS) else False
        vals['instance_until'] = (instance.get('until') or False) if vals['instance_status'] else False
        if anomaly_detected:
            vals['last_clock_anomaly_at'] = now
            _logger.warning(
                "[wb_license_client] Clock-Anomalie erkannt: vorheriger "
                "last_server_check=%s lag in der Zukunft (now=%s). "
                "Mögliche Ursache: System-Uhr zurueckgedreht oder "
                "DB-Snapshot eingespielt.",
                self.last_server_check, now,
            )
        old_state = self.state
        self.write(vals)
        if old_state != vals['state']:
            self._notify_state_change(old_state, vals['state'])

    def _mark_product_mismatch(self):
        """Key or server answer belongs to another product: treat as expired (#97, I4)."""
        self.ensure_one()
        old_state = self.state
        self.sudo().write({
            'state': 'expired',
            'last_server_check': fields.Datetime.now(),
            'last_check_success': False,
            'last_error_message': 'PRODUCT_MISMATCH',
        })
        if old_state != 'expired':
            self._notify_state_change(old_state, 'expired')

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
            'last_failed_check_at': fields.Datetime.now(),
        })
        # Ein Test bleibt 'trial': is_valid endet dann am valid_to statt erst
        # nach der Offline-Toleranz von 'unknown'.
        if self.state == 'active':
            self.state = 'unknown'

    def _notify_state_change(self, old_state, new_state):
        """Bei kritischem Übergang Admin-Mail senden."""
        critical_transitions = {
            ('active', 'grace'): 'wb_license_client.mail_template_grace_started_local',
            ('active', 'expired'): 'wb_license_client.mail_template_expired_local',
            ('trial', 'expired'): 'wb_license_client.mail_template_expired_local',
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
            'name': _("Activate License"),
            'res_model': 'wb.license.activate.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_product_code': self.product_code},
        }

    def action_open_trial_wizard(self):
        """Button: open the wizard in trial mode for this product."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Start 7-day free trial"),
            'res_model': 'wb.license.activate.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_product_code': self.product_code, 'default_mode': 'trial'},
        }

    def action_force_ping(self):
        """Admin-Action: erzwingt einen Server-Ping außerhalb des normalen Cron."""
        self.ensure_one()
        key = self.env['wb.license.client']._get_stored_key(self.product_code)
        if not key:
            raise UserError(_("No key stored for %s.") % self.product_code)
        self.env['wb.license.client']._do_ping(self, self.product_code, key)
