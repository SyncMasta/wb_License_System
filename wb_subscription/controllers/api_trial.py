"""HTTP-Endpoints für die WB-Webseite (wissen-beratung.de).

Diese Routes haben CORS auf *.wissen-beratung.de eingeschränkt — sie sollen
NICHT von beliebigen Kunden-Odoos aus aufgerufen werden, sondern nur vom
eigenen Webseite-Frontend bzw. dem Bestell-Formular.

Authentifizierung der Order-Route:
- Header X-API-Key gegen ir.config_parameter wb_subscription.webshop_api_key
- Wenn nicht gesetzt → 401, kein Order-Eingang möglich
"""

import logging
import re

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

CORS_WB_ONLY = '*.wissen-beratung.de'


EMAIL_RE = re.compile(r'^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$')


def _client_ip():
    return request.httprequest.remote_addr or '0.0.0.0'


def _rate_limit(bucket_key, endpoint, default_max, default_window):
    return request.env['wb.rate.limit.entry'].sudo().check_and_increment(
        bucket_key, endpoint, default_max, default_window,
    )


class ApiTrialController(http.Controller):

    @http.route('/api/license/trial',
                type='json', auth='public', methods=['POST'],
                csrf=False, cors=CORS_WB_ONLY)
    def request_trial(self, **kw):
        """Trial-Anfrage mit Lead-Capture.

        Rate-Limits:
        - 1 Trial pro 24h pro Email
        - 1 Trial pro 24h pro Domain
        """
        email = (kw.get('contact_email') or '').strip().lower()
        company = (kw.get('company_name') or '').strip()
        domain = (kw.get('domain') or '').strip().lower()
        product_code = (kw.get('product_code') or '').strip().upper()

        if not EMAIL_RE.match(email):
            return {'error': 'INVALID_EMAIL'}
        if not company:
            return {'error': 'MISSING_COMPANY'}
        if not domain:
            return {'error': 'MISSING_DOMAIN'}
        if not re.match(r'^[A-Z]{4}$', product_code):
            return {'error': 'INVALID_PRODUCT_CODE'}

        if not _rate_limit(f"email:{email}", 'trial', 1, 86400):
            return {'error': 'EMAIL_LIMIT_REACHED'}
        if not _rate_limit(f"domain:{domain}", 'trial', 1, 86400):
            return {'error': 'DOMAIN_LIMIT_REACHED'}

        product = request.env['product.product'].sudo().search([
            ('wb_technical_code', '=', product_code),
            ('wb_is_license_product', '=', True),
        ], limit=1)
        if not product:
            return {'error': 'PRODUCT_NOT_FOUND'}

        trial = request.env['wb.license.trial.request'].sudo().create({
            'product_id': product.id,
            'contact_name': (kw.get('contact_name') or '').strip()[:100],
            'contact_email': email,
            'contact_phone': (kw.get('contact_phone') or '').strip()[:50],
            'company_name': company[:100],
            'company_size': kw.get('company_size') if kw.get('company_size') in (
                'solo', '2-10', '11-50', '51-250', '251+',
            ) else False,
            'industry': (kw.get('industry') or '').strip()[:100],
            'domain': domain,
            'expected_use_case': (kw.get('expected_use_case') or '').strip()[:1000],
        })

        try:
            trial.action_approve_and_create_trial()
        except Exception as e:
            _logger.exception("[wb_subscription] Trial-Auto-Approve fehlgeschlagen: %s", e)
            return {'error': 'TRIAL_PROCESSING_FAILED'}

        return {
            'status': 'ok',
            'trial_id': trial.name,
            'message': 'Trial-Anfrage angenommen — Schlüssel kommt per Email.',
        }

    @http.route('/api/wb_subscription/order',
                type='json', auth='public', methods=['POST'],
                csrf=False, cors=CORS_WB_ONLY)
    def create_order(self, **kw):
        """Bestellung von wissen-beratung.de.

        Authentifizierung über X-API-Key Header gegen
        ir.config_parameter wb_subscription.webshop_api_key.

        Flow nach DECISION #7 (Odoo-Standards nutzen):
        1. Erzeugt res.partner (oder matched per Email)
        2. Erzeugt sale.order im Draft-State
        3. Tobias prüft + bestätigt manuell in Odoo
        4. Rechnung wird über Standard-Flow versendet (mit Payment-Link
           des konfigurierten Payment-Providers)
        5. Kunde zahlt → account.move.payment_state='paid' → Lizenz wird
           automatisch erzeugt (siehe account_move.py write-Hook)

        KEINE Stripe-spezifische Logik in diesem Modul. Provider wird in
        Odoo unter Sales → Configuration → Payment Providers eingerichtet.
        """
        api_key_header = request.httprequest.headers.get('X-API-Key', '')
        configured_key = request.env['ir.config_parameter'].sudo().get_param(
            'wb_subscription.webshop_api_key')
        if not configured_key:
            _logger.warning("[wb_subscription] Order-Endpoint aufgerufen, "
                            "aber wb_subscription.webshop_api_key nicht konfiguriert")
            return {'error': 'WEBSHOP_NOT_CONFIGURED'}
        if api_key_header != configured_key:
            return {'error': 'UNAUTHORIZED'}

        email = (kw.get('contact_email') or '').strip().lower()
        if not EMAIL_RE.match(email):
            return {'error': 'INVALID_EMAIL'}

        product_code = (kw.get('product_code') or '').strip().upper()
        product = request.env['product.product'].sudo().search([
            ('wb_technical_code', '=', product_code),
            ('wb_is_license_product', '=', True),
        ], limit=1)
        if not product:
            return {'error': 'PRODUCT_NOT_FOUND'}

        partner = request.env['res.partner'].sudo().search([
            ('email', '=ilike', email),
        ], limit=1)
        if not partner:
            partner = request.env['res.partner'].sudo().create({
                'name': (kw.get('contact_name') or '').strip()[:100] or email,
                'email': email,
                'phone': (kw.get('contact_phone') or '').strip()[:50],
                'street': (kw.get('street') or '').strip()[:128],
                'zip': (kw.get('zip') or '').strip()[:24],
                'city': (kw.get('city') or '').strip()[:128],
                'vat': (kw.get('vat') or '').strip()[:32],
                'is_company': bool(kw.get('company_name')),
                'company_type': 'company' if kw.get('company_name') else 'person',
            })

        order = request.env['sale.order'].sudo().create({
            'partner_id': partner.id,
            'order_line': [(0, 0, {
                'product_id': product.id,
                'product_uom_qty': 1,
                'name': product.name,
                'price_unit': product.list_price,
            })],
            'note': f"Webshop-Bestellung. Domain: {kw.get('domain', '-')}",
        })

        return {
            'status': 'ok',
            'order_id': order.id,
            'order_name': order.name,
            'partner_id': partner.id,
            'message': (
                'Bestellung angelegt. Sie erhalten die Rechnung mit Zahlungs-Link '
                'per E-Mail. Nach Zahlungseingang wird Ihre Lizenz automatisch '
                'eingerichtet und die Aktivierungs-Anleitung verschickt.'
            ),
        }
