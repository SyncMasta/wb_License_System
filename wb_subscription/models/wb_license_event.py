"""Audit-Log für alle lizenz-relevanten Events.

Wichtig: Der Audit-Log ist immutable — einmal geschrieben, wird er
nicht mehr geändert. Deshalb kein Write-Zugriff für User, selbst
Manager darf nur lesen und (zur Not) löschen.
"""

import json
import logging

from odoo import _, api, fields, models

from ._redact import redact_secrets

_logger = logging.getLogger(__name__)


EVENT_TYPES = [
    ('key_generated', 'Key generiert'),
    ('activation', 'Erstaktivierung erfolgreich'),
    ('activation_failed', 'Aktivierung fehlgeschlagen'),
    ('ticket_viewed', 'Ticket-Link geöffnet'),
    ('ticket_ip_mismatch', 'Ticket-IP-Mismatch'),
    ('otp_sent', 'Email-OTP versendet'),
    ('otp_send_failed', 'Email-OTP-Versand fehlgeschlagen'),
    ('otp_verified', 'OTP korrekt eingegeben'),
    ('otp_failed', 'OTP falsch'),
    ('code_revealed', 'Code im Portal angezeigt'),
    ('ping', 'Ping vom Client'),
    ('renewal', 'Jahres-Verlängerung'),
    ('trial_started', 'Trial gestartet'),
    ('trial_converted', 'Trial zu Paid konvertiert'),
    ('trial_expired', 'Trial abgelaufen'),
    ('grace_started', 'Grace-Period gestartet'),
    ('grace_ended', 'Grace-Period beendet'),
    ('migration_requested', 'Migration angefragt'),
    ('migration_approved', 'Migration genehmigt'),
    ('migration_rejected', 'Migration abgelehnt'),
    ('revoked', 'Lizenz gesperrt'),
    ('reactivated', 'Lizenz reaktiviert'),
    ('certificate_generated', 'Zertifikat erzeugt'),
    ('lead_received', 'Lead/Verkaufschance vom Kunden eingegangen'),
    ('eula_accepted', 'EULA beim Activate bestätigt'),
    ('terms_accepted', 'AGB beim Activate bestätigt'),
    ('privacy_accepted', 'Datenschutzhinweis beim Activate bestätigt'),
    ('refund_waiver_confirmed', 'Verzicht auf Gutschrift bestätigt'),
    ('newsletter_optin', 'Newsletter-Opt-In beim Activate'),
    ('newsletter_optin_failed', 'Newsletter-Opt-In fehlgeschlagen (Liste fehlt o.ä.)'),
    ('download', 'Modul-Tarball aus Kunden-Portal heruntergeladen'),
]


class WbLicenseEvent(models.Model):
    _name = 'wb.license.event'
    _description = 'Lizenz-Event (Audit-Log)'
    _order = 'timestamp desc, id desc'
    _rec_name = 'event_type'

    license_id = fields.Many2one(
        'wb.license.key',
        string='Lizenz',
        ondelete='cascade',
        index=True,
    )
    event_type = fields.Selection(EVENT_TYPES, required=True, index=True)
    timestamp = fields.Datetime(required=True, default=fields.Datetime.now, index=True)
    ip_address = fields.Char()
    user_agent = fields.Char()
    domain = fields.Char()
    db_uuid = fields.Char()
    details = fields.Text(
        help="Zusätzliche Event-Details als JSON-Text.",
    )
    created_by_id = fields.Many2one('res.users', string='Ausgelöst durch')

    @api.model
    def log_event(self, license, event_type, **kwargs):
        """Komfort-Methode um Events zu loggen.

        Klartext-Codes oder andere Secrets dürfen hier NICHT landen —
        das Audit-Log ist eher öffentlich als geheim.

        Args:
            license: wb.license.key Record oder False (für orphaned Events)
            event_type: einer der EVENT_TYPES
            **kwargs: ip_address, user_agent, domain, db_uuid, details (dict)
        """
        details = kwargs.pop('details', None)
        if details and not isinstance(details, str):
            details = json.dumps(details, default=str, ensure_ascii=False)
        # Sprint 2 / B-M1: details können versehentlich Secret-Patterns
        # enthalten (z.B. wenn ein activation_code in einem Fehler-Payload
        # landet). Vor dem Persist redacten — Audit-Log ist immutable.
        details = redact_secrets(details)
        vals = {
            'license_id': license.id if license else False,
            'event_type': event_type,
            'ip_address': kwargs.get('ip_address'),
            'user_agent': kwargs.get('user_agent'),
            'domain': kwargs.get('domain'),
            'db_uuid': kwargs.get('db_uuid'),
            'details': details,
            'created_by_id': self.env.uid if self.env.uid else False,
        }
        return self.sudo().create(vals)

    def write(self, vals):
        """Verbiete Änderungen an bereits angelegten Events (außer für technical_flag)."""
        if self.env.su:
            return super().write(vals)
        raise models.AccessError(_(
            "Events im Audit-Log können nicht geändert werden."
        ))
