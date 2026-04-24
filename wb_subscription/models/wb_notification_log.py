"""Notification-Log für Compliance und Fehlersuche.

Protokolliert jede Nachricht die aus wb_subscription an Kunden oder
an Tobias rausgeht (Email, Telegram). Getrennt von mail.message, weil
dort auch andere Systeme reinschreiben.
"""

from odoo import fields, models


CHANNELS = [
    ('email', 'Email'),
    ('telegram', 'Telegram'),
    ('sms', 'SMS'),
]

STATES = [
    ('pending', 'Pending'),
    ('sent', 'Sent'),
    ('failed', 'Failed'),
]


class WbNotificationLog(models.Model):
    _name = 'wb.notification.log'
    _description = 'WB Benachrichtigungs-Log'
    _order = 'create_date desc'

    name = fields.Char(required=True)
    channel = fields.Selection(CHANNELS, required=True, index=True)
    state = fields.Selection(STATES, required=True, default='pending', index=True)
    template_xmlid = fields.Char(string='Template XML-ID')
    recipient = fields.Char(string='Empfänger (Email/Chat-ID)', required=True)
    subject = fields.Char()
    body_preview = fields.Text(help="Gekürzter Body (erste 500 Zeichen).")
    license_id = fields.Many2one('wb.license.key', ondelete='set null')
    partner_id = fields.Many2one('res.partner', ondelete='set null')
    error_message = fields.Text()
    sent_at = fields.Datetime()

    def mark_sent(self):
        self.write({'state': 'sent', 'sent_at': fields.Datetime.now()})

    def mark_failed(self, error_message):
        self.write({'state': 'failed', 'error_message': error_message})
