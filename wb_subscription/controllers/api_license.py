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

        Pflicht-Consents (alle vier müssen ``True`` sein, sonst MISSING_CONSENTS):

        * ``confirm_eula`` — EULA bestätigt
        * ``confirm_terms`` — AGB bestätigt
        * ``confirm_privacy`` — Datenschutzhinweis bestätigt
        * ``confirm_no_refund`` — Verzicht auf Gutschrift bestätigt

        Optional:

        * ``subscribe_newsletter`` — Newsletter-Opt-In
        * ``contact_email`` — Email für Newsletter und Audit-Bezug

        Rate-Limit: 5/h pro IP gegen Code-Brute-Force.
        """
        if not _check_rate_limit(_client_ip(), 'activate', 5, 3600):
            return {'error': 'TOO_MANY_ATTEMPTS'}

        key = (kw.get('key') or '').strip()
        code = (kw.get('activation_code') or '').strip()
        domain = (kw.get('domain') or '').strip()
        db_uuid = (kw.get('db_uuid') or '').strip()
        contact_email = (kw.get('contact_email') or kw.get('email') or '').strip()

        gen = request.env['wb.key.generator'].sudo()
        if not gen.validate_key_format(key):
            return {'error': 'INVALID_KEY_FORMAT'}
        if not gen.validate_activation_code_format(code):
            return {'error': 'INVALID_CODE_FORMAT'}
        if not domain or not db_uuid:
            return {'error': 'MISSING_BINDING_DATA'}

        # Pflicht-Consents — VOR DB-Lookup prüfen, damit der Code-Hash
        # nicht angetastet wird wenn der Anwender es vergessen hat.
        required_consent_keys = (
            'confirm_eula', 'confirm_terms',
            'confirm_privacy', 'confirm_no_refund',
        )
        missing = [k for k in required_consent_keys if not kw.get(k)]
        if missing:
            return {
                'error': 'MISSING_CONSENTS',
                'missing': missing,
            }

        license = request.env['wb.license.key'].sudo().search([('name', '=', key)], limit=1)
        if not license:
            return {'error': 'KEY_NOT_FOUND'}

        consents = {
            'eula': bool(kw.get('confirm_eula')),
            'terms': bool(kw.get('confirm_terms')),
            'privacy': bool(kw.get('confirm_privacy')),
            'refund_waiver': bool(kw.get('confirm_no_refund')),
            'newsletter': bool(kw.get('subscribe_newsletter')),
            'email': contact_email,
        }
        result = license.activate_with_code(
            code, domain, db_uuid,
            ip=_client_ip(), user_agent=_client_ua(),
            consents=consents,
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

    @http.route('/api/license/announce',
                type='json', auth='public', methods=['POST'],
                csrf=False, cors=CORS_ANY)
    def announce_install(self, **kw):
        """Lead-Registry — wird vom Client beim Install/Boot eines unlizenzierten
        Produkt-Addons aufgerufen. Trägt (product_code, domain, db_uuid) ein
        bzw. updated last_seen_at + announce_count.

        Rate-Limit: 20/h pro IP. Höher als /activate, weil pro Install-Boot
        und parallel mehrerer Produkt-Addons mehrere Calls eingehen können.
        """
        if not _check_rate_limit(_client_ip(), 'announce', 20, 3600):
            return {'error': 'TOO_MANY_REQUESTS'}

        product_code = (kw.get('product_code') or '').strip().upper()
        domain = (kw.get('domain') or '').strip()
        db_uuid = (kw.get('db_uuid') or '').strip()

        if not product_code or len(product_code) != 4:
            return {'error': 'INVALID_PRODUCT_CODE'}
        if not domain or not db_uuid:
            return {'error': 'MISSING_BINDING_DATA'}

        request.env['wb.license.install'].sudo().announce(
            product_code, domain, db_uuid,
            contact_email=(kw.get('email') or '').strip() or None,
            client_version=(kw.get('client_version') or '').strip() or None,
            ip=_client_ip(),
            user_agent=_client_ua(),
        )
        return {'status': 'ok'}

    @http.route('/api/license/lead',
                type='json', auth='public', methods=['POST'],
                csrf=False, cors=CORS_ANY)
    def submit_lead(self, **kw):
        """Lead-/Verkaufschancen-Eingang aus dem Kunden-Wizard.

        Wird von wb_license_client aufgerufen wenn ein Kunde im Pro-Modul
        entweder den Onboarding-Wizard absendet (intent='info') oder die
        Lizenz-Anfrage abschickt (intent='purchase').

        Bei intent='purchase' wird ein crm.lead vom Typ 'opportunity'
        (Verkaufschance) erzeugt, sonst ein normaler 'lead'.

        Rate-Limit: 5/h pro IP gegen Spam.
        """
        if not _check_rate_limit(_client_ip(), 'lead', 5, 3600):
            return {'error': 'TOO_MANY_REQUESTS'}

        product_code = (kw.get('product_code') or '').strip().upper()
        domain = (kw.get('domain') or '').strip()
        db_uuid = (kw.get('db_uuid') or '').strip()
        contact_email = (kw.get('contact_email') or '').strip()
        contact_name = (kw.get('contact_name') or '').strip()
        company_name = (kw.get('company_name') or '').strip()
        intent = kw.get('intent') if kw.get('intent') in ('info', 'purchase') else 'info'

        if not product_code or len(product_code) != 4:
            return {'error': 'INVALID_PRODUCT_CODE'}
        if not domain or not db_uuid:
            return {'error': 'MISSING_BINDING_DATA'}
        if intent == 'purchase':
            if not contact_email or not contact_name or not company_name:
                return {'error': 'MISSING_REQUIRED_FIELDS'}

        payload = {
            'product_code': product_code,
            'domain': domain,
            'db_uuid': db_uuid,
            'intent': intent,
            'contact_email': contact_email,
            'contact_name': contact_name,
            'contact_phone': (kw.get('contact_phone') or '').strip(),
            'company_name': company_name,
            'company_vat': (kw.get('company_vat') or '').strip(),
            'company_street': (kw.get('company_street') or '').strip(),
            'company_zip': (kw.get('company_zip') or '').strip(),
            'company_city': (kw.get('company_city') or '').strip(),
            'company_country_code': (kw.get('company_country_code') or '').strip(),
            'notes': kw.get('notes') or '',
            'client_version': (kw.get('client_version') or '').strip(),
            '_ip': _client_ip(),
            '_user_agent': _client_ua(),
        }

        install = request.env['wb.license.install'].sudo().submit_lead(payload)

        try:
            request.env['wb.license.event'].sudo().log_event(
                False, 'lead_received',
                ip_address=_client_ip(), user_agent=_client_ua(),
                domain=domain, db_uuid=db_uuid,
                details='intent=%s product=%s install_id=%s lead=%s' % (
                    intent, product_code, install.id,
                    install.crm_lead_id.id if install.crm_lead_id else '-'),
            )
        except Exception as exc:
            _logger.warning("[wb_subscription] lead event-log failed: %s", exc)

        return {
            'status': 'ok',
            'install_id': install.id,
            'lead_id': install.crm_lead_id.id if install.crm_lead_id else False,
            'intent': intent,
        }

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
