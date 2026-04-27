"""HTTP-Endpoints für Kunden-Clients.

Alle Endpoints:
- type='json' (kein HTML)
- auth='public' (kein Login — Auth erfolgt über Key+Code bzw. Format-Check)
- csrf=False (API-Calls)
- cors='*' (Kunden-Odoos haben beliebige Domains)

Rate-Limiting läuft über wb.rate.limit.entry. Limits werden aus
ir.config_parameter geladen mit Defaults.

Sicherheits-Kritisch:
- Activation-Code wird NICHT geloggt
- Bei jedem Fehlversuch wird ein wb.license.event-Eintrag mit IP angelegt
- Format-Check vor DB-Zugriff (cheap CPU)
"""

import logging

from odoo import http
from odoo.http import request, Response

_logger = logging.getLogger(__name__)

CORS_ANY = '*'


def _client_ip():
    return request.httprequest.remote_addr or '0.0.0.0'


def _client_ua():
    return request.httprequest.user_agent.string[:255] if request.httprequest.user_agent else ''


def _check_rate_limit(bucket_prefix, endpoint, default_max, default_window):
    """Wrapper um wb.rate.limit.entry.check_and_increment.

    Returns True if allowed, False if exceeded.
    """
    icp = request.env['ir.config_parameter'].sudo()
    raw = icp.get_param(f'wb_subscription.rate_limit_{endpoint}')
    max_count, window_seconds = default_max, default_window
    if raw:
        try:
            parts = raw.split('/')
            max_count, window_seconds = int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            pass
    bucket_key = f"{endpoint}:{bucket_prefix}"
    return request.env['wb.rate.limit.entry'].sudo().check_and_increment(
        bucket_key, endpoint, max_count, window_seconds,
    )


class ApiLicenseController(http.Controller):

    @http.route('/api/license/check',
                type='json', auth='public', methods=['POST'],
                csrf=False, cors=CORS_ANY)
    def check_license(self, **kw):
        """Täglicher Ping — gibt aktuellen Status zurück.

        Rate-Limit: 100/h pro IP. Wird geloggt aber nicht in events.
        """
        if not _check_rate_limit(_client_ip(), 'check', 100, 3600):
            return {'error': 'TOO_MANY_REQUESTS'}

        key = (kw.get('key') or '').strip()
        gen = request.env['wb.key.generator'].sudo()
        if not gen.validate_key_format(key):
            return {'error': 'INVALID_KEY_FORMAT'}

        license = request.env['wb.license.key'].sudo().search([('name', '=', key)], limit=1)
        if not license:
            return {'error': 'KEY_NOT_FOUND'}

        license.record_ping(ip=_client_ip(), user_agent=_client_ua())
        request.env['wb.license.event'].sudo().log_event(
            license, 'ping',
            ip_address=_client_ip(), user_agent=_client_ua(),
            domain=kw.get('domain'), db_uuid=kw.get('db_uuid'),
        )
        return {
            'state': license.state,
            'valid_from': license.valid_from.isoformat() if license.valid_from else None,
            'valid_to': license.valid_to.isoformat() if license.valid_to else None,
            'grace_until': license.grace_until.isoformat() if license.grace_until else None,
            'product_code': license.product_code,
        }

    @http.route('/api/license/activate',
                type='json', auth='public', methods=['POST'],
                csrf=False, cors=CORS_ANY)
    def activate_license(self, **kw):
        """Erstaktivierung mit Key + Activation-Code.

        Rate-Limit: 5/h pro IP gegen Code-Brute-Force.
        """
        if not _check_rate_limit(_client_ip(), 'activate', 5, 3600):
            return {'error': 'TOO_MANY_ATTEMPTS'}

        key = (kw.get('key') or '').strip()
        code = (kw.get('activation_code') or '').strip()
        domain = (kw.get('domain') or '').strip()
        db_uuid = (kw.get('db_uuid') or '').strip()

        gen = request.env['wb.key.generator'].sudo()
        if not gen.validate_key_format(key):
            return {'error': 'INVALID_KEY_FORMAT'}
        if not gen.validate_activation_code_format(code):
            return {'error': 'INVALID_CODE_FORMAT'}
        if not domain or not db_uuid:
            return {'error': 'MISSING_BINDING_DATA'}

        license = request.env['wb.license.key'].sudo().search([('name', '=', key)], limit=1)
        if not license:
            return {'error': 'KEY_NOT_FOUND'}

        result = license.activate_with_code(
            code, domain, db_uuid,
            ip=_client_ip(), user_agent=_client_ua(),
        )
        if result['status'] == 'ok':
            for ticket in license.ticket_ids.filtered(lambda t: t.state in ('pending', 'awaiting_otp', 'code_revealed')):
                ticket.action_consume()
            return {
                'status': 'ok',
                'state': license.state,
                'bound_domain': license.bound_domain,
                'valid_to': license.valid_to.isoformat() if license.valid_to else None,
            }
        return result

    @http.route('/api/license/migrate',
                type='json', auth='public', methods=['POST'],
                csrf=False, cors=CORS_ANY)
    def request_migration(self, **kw):
        """Migrations-Antrag.

        Rate-Limit: 2/Tag pro Key (Spam-Schutz).
        """
        key = (kw.get('key') or '').strip()
        if not _check_rate_limit(key, 'migrate', 2, 86400):
            return {'error': 'TOO_MANY_REQUESTS'}

        license = request.env['wb.license.key'].sudo().search([('name', '=', key)], limit=1)
        if not license:
            return {'error': 'KEY_NOT_FOUND'}

        request.env['wb.license.migration.request'].sudo().create({
            'license_id': license.id,
            'current_domain': kw.get('current_domain') or license.bound_domain or '',
            'current_db_uuid': kw.get('current_db_uuid') or license.bound_db_uuid or '',
            'new_domain': (kw.get('new_domain') or '').strip(),
            'new_db_uuid': (kw.get('new_db_uuid') or '').strip(),
            'reason': kw.get('reason') or '',
            'contact_email': kw.get('contact_email') or license.partner_id.email or '',
        })
        return {'status': 'ok', 'message': 'Migrations-Antrag eingegangen.'}
