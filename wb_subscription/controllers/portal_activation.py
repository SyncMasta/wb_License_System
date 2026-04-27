"""Portal-Routes für Activation-Code-Download.

Flow:
1. GET  /activate/<token>            → Landing, IP-Binding setzen, OTP-Anfrage-Form
2. POST /activate/<token>/send-otp   → OTP per Email versenden
3. POST /activate/<token>/verify-otp → OTP prüfen, Code für 10min anzeigen

Sicherheit:
- IP-Binding nach DECISION #48c
- Rate-Limit: 5 OTP-Verify-Versuche pro Ticket
- Code-Anzeige nur 10min, danach state='consumed' und encrypted_code=False
"""

import logging

from odoo import http, _
from odoo.exceptions import UserError
from odoo.http import request

_logger = logging.getLogger(__name__)


def _get_ticket(token):
    return request.env['wb.activation.ticket'].sudo().search(
        [('name', '=', token)], limit=1,
    )


class PortalActivationController(http.Controller):

    @http.route('/activate/<string:token>',
                type='http', auth='public', methods=['GET'], csrf=False)
    def activation_landing(self, token, **kw):
        """Landing-Page: zeigt Email-Maske und Send-OTP-Button."""
        ticket = _get_ticket(token)
        if not ticket:
            return request.render(
                'wb_subscription.portal_ticket_invalid',
                {'reason': 'Ticket nicht gefunden.'},
            )
        try:
            ticket.register_landing(
                ip=request.httprequest.remote_addr or '',
                user_agent=(request.httprequest.user_agent.string or '')[:255]
                if request.httprequest.user_agent else '',
            )
        except UserError as e:
            return request.render(
                'wb_subscription.portal_ticket_invalid',
                {'reason': str(e)},
            )

        return request.render(
            'wb_subscription.portal_activation_landing',
            {
                'ticket': ticket,
                'email_masked': _mask_email(ticket.email),
            },
        )

    @http.route('/activate/<string:token>/send-otp',
                type='http', auth='public', methods=['POST'], csrf=False)
    def send_otp(self, token, **kw):
        ticket = _get_ticket(token)
        if not ticket:
            return request.render(
                'wb_subscription.portal_ticket_invalid',
                {'reason': 'Ticket nicht gefunden.'},
            )
        try:
            ticket.send_otp(ip=request.httprequest.remote_addr or '')
        except UserError as e:
            return request.render(
                'wb_subscription.portal_ticket_invalid',
                {'reason': str(e)},
            )

        return request.render(
            'wb_subscription.portal_activation_otp_form',
            {
                'ticket': ticket,
                'email_masked': _mask_email(ticket.email),
                'error': None,
            },
        )

    @http.route('/activate/<string:token>/verify-otp',
                type='http', auth='public', methods=['POST'], csrf=False)
    def verify_otp(self, token, **kw):
        ticket = _get_ticket(token)
        if not ticket:
            return request.render(
                'wb_subscription.portal_ticket_invalid',
                {'reason': 'Ticket nicht gefunden.'},
            )

        otp_input = (kw.get('otp') or '').strip()
        try:
            code = ticket.verify_otp(
                otp_input, ip=request.httprequest.remote_addr or '',
            )
        except UserError as e:
            return request.render(
                'wb_subscription.portal_ticket_invalid',
                {'reason': str(e)},
            )

        if not code:
            return request.render(
                'wb_subscription.portal_activation_otp_form',
                {
                    'ticket': ticket,
                    'email_masked': _mask_email(ticket.email),
                    'error': 'OTP falsch — bitte erneut eingeben.',
                },
            )

        return request.render(
            'wb_subscription.portal_activation_code_reveal',
            {
                'ticket': ticket,
                'activation_code': code,
                'license_key': ticket.license_id.name,
                'product_name': ticket.license_id.product_id.name,
            },
        )


def _mask_email(email):
    """foo@bar.de → f**@bar.de"""
    if not email or '@' not in email:
        return email or ''
    local, domain = email.split('@', 1)
    if len(local) <= 1:
        return f"{local}@{domain}"
    return f"{local[0]}{'*' * max(1, len(local) - 1)}@{domain}"
