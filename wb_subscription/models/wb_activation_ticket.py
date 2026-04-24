"""Portal-Ticket für Activation-Code-Download.

Flow (siehe ARCHITECTURE.md 4.7 + 9.1):
1. Ticket wird beim Key-Create erzeugt (encrypted_code via Fernet gespeichert)
2. Kunde klickt Email-Link → GET /activate/<ticket_token>
3. Portal pinnt IP bei erstem Aufruf
4. Kunde fordert OTP an → Email-OTP wird generiert und versendet
5. Kunde gibt OTP ein → Code wird dekryptiert und für 10min angezeigt
6. Nach Klartext-Anzeige läuft Countdown, state='code_revealed'
7. Nach Activation im Client: state='consumed', encrypted_code = False
"""

import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


TICKET_TTL_HOURS = 72
OTP_TTL_MINUTES = 10
CODE_DISPLAY_MINUTES = 10
OTP_MAX_ATTEMPTS = 5
IP_MISMATCH_THRESHOLD = 3

TICKET_STATES = [
    ('pending', 'Pending (wartet auf ersten Aufruf)'),
    ('awaiting_otp', 'Awaiting OTP (Link geöffnet, OTP angefragt)'),
    ('code_revealed', 'Code Revealed (sichtbar für 10 min)'),
    ('consumed', 'Consumed (Code verwendet)'),
    ('expired', 'Expired (72h überschritten)'),
    ('revoked', 'Revoked (zu viele IP-Mismatches oder Admin)'),
]


class WbActivationTicket(models.Model):
    _name = 'wb.activation.ticket'
    _description = 'Activation-Ticket für Portal-Download des Codes'
    _order = 'create_date desc'

    name = fields.Char(
        string='Ticket-Token',
        required=True,
        copy=False,
        index=True,
        readonly=True,
    )
    license_id = fields.Many2one(
        'wb.license.key',
        required=True,
        ondelete='cascade',
        index=True,
    )
    email = fields.Char(
        required=True,
        help="Email-Adresse an die der OTP gesendet wird. Wird aus partner_id des Keys übernommen.",
    )
    state = fields.Selection(TICKET_STATES, required=True, default='pending', index=True)

    encrypted_code = fields.Binary(
        string='Verschlüsselter Code (Fernet)',
        attachment=False,
        help="Fernet-verschlüsselter Activation-Code. Wird nach 'consumed' gelöscht.",
        groups='wb_subscription.group_wb_subscription_manager',
    )

    current_otp_hash = fields.Binary(
        string='OTP-Hash (bcrypt)',
        attachment=False,
        groups='wb_subscription.group_wb_subscription_manager',
    )
    otp_sent_at = fields.Datetime()
    otp_valid_until = fields.Datetime()
    otp_attempts = fields.Integer(default=0)
    otp_max_attempts = fields.Integer(default=OTP_MAX_ATTEMPTS)

    code_revealed_at = fields.Datetime()
    code_hidden_at = fields.Datetime()

    bound_ip = fields.Char(
        string='Gebundene IP',
        help="IP beim ersten Aufruf von /activate/<ticket>. Folgende Requests müssen "
             "von derselben IP kommen (DECISION #48c).",
    )
    ip_mismatch_count = fields.Integer(default=0)
    last_accessed_ip = fields.Char()

    expires_at = fields.Datetime(
        required=True,
        help="Ticket ist nach dieser Zeit ungültig (72h nach create_date).",
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name'):
                vals['name'] = self.env['wb.key.generator'].generate_ticket_token()
            if not vals.get('expires_at'):
                vals['expires_at'] = fields.Datetime.now() + timedelta(hours=TICKET_TTL_HOURS)
        return super().create(vals_list)

    def _check_alive(self):
        """Raised UserError wenn Ticket nicht mehr benutzbar ist."""
        self.ensure_one()
        now = fields.Datetime.now()
        if self.state in ('consumed', 'revoked', 'expired'):
            raise UserError(_("Dieses Ticket ist nicht mehr gültig (Status: %s).") % self.state)
        if self.expires_at and self.expires_at < now:
            self.state = 'expired'
            raise UserError(_("Das Ticket ist abgelaufen."))

    def register_landing(self, ip, user_agent=None):
        """Wird vom GET /activate/<ticket> aufgerufen.

        - Bei erstem Aufruf: bound_ip setzen
        - Bei späterem Aufruf: IP-Check, Mismatch zählen/blocken
        - State auf 'awaiting_otp' setzen wenn bisher 'pending'
        """
        self.ensure_one()
        self._check_alive()
        self.last_accessed_ip = ip

        if not self.bound_ip:
            self.bound_ip = ip
        elif self.bound_ip != ip:
            self.ip_mismatch_count += 1
            self.env['wb.license.event'].log_event(
                self.license_id, 'ticket_ip_mismatch',
                ip_address=ip, user_agent=user_agent,
                details={
                    'ticket_id': self.id,
                    'bound_ip': self.bound_ip,
                    'actual_ip': ip,
                    'mismatch_count': self.ip_mismatch_count,
                },
            )
            if self.ip_mismatch_count >= IP_MISMATCH_THRESHOLD:
                self.state = 'revoked'
                raise UserError(_(
                    "Zu viele Zugriffe von unterschiedlichen Netzwerken — "
                    "Ticket wurde aus Sicherheitsgründen gesperrt. Bitte "
                    "Support kontaktieren."
                ))

        if self.state == 'pending':
            self.state = 'awaiting_otp'
        self.env['wb.license.event'].log_event(
            self.license_id, 'ticket_viewed',
            ip_address=ip, user_agent=user_agent,
            details={'ticket_id': self.id},
        )

    def send_otp(self, ip):
        """Generiert neuen OTP, hasht ihn, versendet per Email."""
        self.ensure_one()
        self._check_alive()
        if self.bound_ip and self.bound_ip != ip:
            raise UserError(_("IP-Wechsel nicht erlaubt für diesen Schritt."))

        gen = self.env['wb.key.generator']
        otp = gen.generate_email_otp()
        self.write({
            'current_otp_hash': gen.hash_otp(otp),
            'otp_sent_at': fields.Datetime.now(),
            'otp_valid_until': fields.Datetime.now() + timedelta(minutes=OTP_TTL_MINUTES),
            'otp_attempts': 0,
        })
        self.env['wb.license.event'].log_event(
            self.license_id, 'otp_sent',
            ip_address=ip,
            details={'ticket_id': self.id},
        )
        return otp

    def verify_otp(self, otp_input, ip):
        """Prüft OTP. Bei Erfolg wird der Klartext-Code zurückgegeben.

        WICHTIG: Der zurückgegebene Code darf NUR direkt ins Portal-Template
        eingehen — nicht in Session speichern, nicht loggen, nicht
        persistieren.
        """
        self.ensure_one()
        self._check_alive()
        if self.bound_ip and self.bound_ip != ip:
            raise UserError(_("IP-Wechsel nicht erlaubt für diesen Schritt."))
        if not self.current_otp_hash:
            raise UserError(_("Kein OTP aktiv — bitte zuerst OTP anfordern."))
        if self.otp_valid_until and self.otp_valid_until < fields.Datetime.now():
            raise UserError(_("OTP abgelaufen. Bitte neuen OTP anfordern."))
        if self.otp_attempts >= self.otp_max_attempts:
            self.state = 'revoked'
            raise UserError(_(
                "Maximale Anzahl Fehlversuche erreicht — Ticket wurde gesperrt."
            ))

        gen = self.env['wb.key.generator']
        if not gen.verify_otp(otp_input, self.current_otp_hash):
            self.otp_attempts += 1
            self.env['wb.license.event'].log_event(
                self.license_id, 'otp_failed',
                ip_address=ip,
                details={'ticket_id': self.id, 'attempt': self.otp_attempts},
            )
            return None

        code = gen.decrypt_code(self.encrypted_code)
        self.write({
            'state': 'code_revealed',
            'code_revealed_at': fields.Datetime.now(),
            'code_hidden_at': fields.Datetime.now() + timedelta(minutes=CODE_DISPLAY_MINUTES),
            'current_otp_hash': False,
        })
        self.env['wb.license.event'].log_event(
            self.license_id, 'otp_verified',
            ip_address=ip,
            details={'ticket_id': self.id},
        )
        self.env['wb.license.event'].log_event(
            self.license_id, 'code_revealed',
            ip_address=ip,
            details={'ticket_id': self.id},
        )
        return code

    def action_consume(self):
        """Wird nach erfolgreicher Activation aufgerufen.

        Löscht encrypted_code aus der DB — minimiert Angriffsfenster
        im Fall eines späteren DB-Leaks.
        """
        for rec in self:
            rec.write({
                'state': 'consumed',
                'encrypted_code': False,
                'current_otp_hash': False,
            })

    @api.model
    def _cron_cleanup_expired(self):
        """Täglicher Cleanup: markiert abgelaufene Tickets und löscht encrypted_code."""
        expired = self.search([
            ('state', 'in', ['pending', 'awaiting_otp', 'code_revealed']),
            ('expires_at', '<', fields.Datetime.now()),
        ])
        expired.write({'state': 'expired', 'encrypted_code': False, 'current_otp_hash': False})
        _logger.info("[wb_subscription] Ticket-Cleanup: %d expired", len(expired))
