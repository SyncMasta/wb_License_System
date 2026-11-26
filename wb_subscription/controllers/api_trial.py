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

import hmac

from odoo import fields, http
from odoo.http import request

from .api_license import _check_rate_limit

_logger = logging.getLogger(__name__)

CORS_WB_ONLY = '*.wissen-beratung.de'


EMAIL_RE = re.compile(r'^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$')
UUID_RE = re.compile(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')


def _client_ip():
    return request.httprequest.remote_addr or '0.0.0.0'


def _text(value):
    """JSON-Feld als String; andere Typen (Zahl, Liste, null) zählen als leer."""
    return value if isinstance(value, str) else ''


def _rate_limit(bucket_key, endpoint, default_max, default_window):
    return request.env['wb.rate.limit.entry'].sudo().check_and_increment(
        bucket_key, endpoint, default_max, default_window,
    )


class ApiTrialController(http.Controller):

    @http.route('/api/license/trial',
                type='json', auth='public', methods=['POST'],
                csrf=False, cors=CORS_WB_ONLY)
    def request_trial(self, **kw):
        """Trial-Anfrage mit Lead-Capture, Freigabe per Klick (DECISIONS.md #94).

        Aufrufer: der Kunden-Odoo (wb_license_client, Knopf „Start 7-day free
        trial“, nur nach ausdrücklicher Zustimmung) und die Website.

        Ablauf:
        - Formatprüfung, Rate-Limit je IP (Standard 5/h, Parameter
          ``wb_subscription.rate_limit_trial_ip``)
        - 1 Test je Produkt und E-Mail, Domain bzw. DB-UUID, unbefristet. Kommt
          derselbe Antrag (gleiche E-Mail, Domain und DB-UUID) erneut, wird der
          vorhandene Stand geliefert (Schlüssel bzw. ``pending``) statt eines
          zweiten Tests — so geht bei einem Netzwerkfehler nichts verloren.
        - Missbrauchsverdacht → ``status: pending`` ohne Schlüssel, Freigabe von
          Hand im Backend
        - sonst sofort frei: ``status: ok`` mit ``key``, ``valid_to`` und
          ``max_users``. Kein Activation-Code (#28), kein API-Secret.
        """
        if not _check_rate_limit(_client_ip(), 'trial_ip', 5, 3600):
            return {'error': 'TOO_MANY_REQUESTS'}

        email = _text(kw.get('contact_email')).strip().lower()
        company = _text(kw.get('company_name')).strip()
        domain = _text(kw.get('domain')).strip().lower()
        db_uuid = _text(kw.get('db_uuid')).strip()
        product_code = _text(kw.get('product_code')).strip().upper()

        if not EMAIL_RE.match(email) or len(email) > 254:
            return {'error': 'INVALID_EMAIL'}
        if not company:
            return {'error': 'MISSING_COMPANY'}
        if not domain or len(domain) > 255:
            return {'error': 'MISSING_DOMAIN'}
        if not re.match(r'^[A-Z0-9]{4}$', product_code):
            return {'error': 'INVALID_PRODUCT_CODE'}
        if db_uuid and not UUID_RE.match(db_uuid):
            return {'error': 'INVALID_DB_UUID'}
        # Ausdrückliche Zustimmung des Antragstellers, mit Fassung der
        # Datenschutzhinweise als Nachweis (#97, I6). Nur echtes JSON-true zählt.
        privacy_version = _text(kw.get('privacy_version')).strip()[:32]
        if kw.get('consent') is not True or not privacy_version:
            return {'error': 'MISSING_CONSENT'}

        product = request.env['product.product'].sudo()._wb_trial_variant(product_code)
        if not product:
            return {'error': 'PRODUCT_NOT_FOUND'}
        if product.wb_trial_days <= 0:
            return {'error': 'TRIAL_NOT_AVAILABLE'}

        Trial = request.env['wb.license.trial.request'].sudo()
        previous = Trial._find_previous(product_code, email, domain, db_uuid)
        same_db = previous['db_uuid']
        if same_db:
            # Diese Datenbank hatte schon einen Test. Den vorhandenen Stand
            # (Schlüssel bzw. pending) gibt es nur für exakt denselben Antrag
            # aus derselben Datenbank; sonst ein einziger, neutraler Code (#97, I2).
            header_db = (request.httprequest.headers.get('X-WB-DB-UUID') or '').strip()
            if (header_db == db_uuid and same_db.contact_email == email
                    and (same_db.domain or '') == domain):
                return same_db._response()
            return {'error': 'TRIAL_NOT_AVAILABLE'}
        # Gleiche E-Mail oder öffentliche Domain aus einer anderen Datenbank:
        # nicht endgültig sperren, sondern prüfen (#97, I3).
        collision = previous['email'] or previous['domain']
        collision_reason = (
            "Gleiche E-Mail oder Domain wie %s (andere Datenbank)" % collision.name
            if collision else False)

        # 24-h-Bucket je Datenbank (ohne DB-UUID je E-Mail): schützt vor
        # parallelen Doppelanträgen, bevor der erste Datensatz committed ist.
        if not _rate_limit(f"db:{product_code}:{db_uuid or email}", 'trial', 1, 86400):
            return {'error': 'TRIAL_NOT_AVAILABLE'}

        ip = _client_ip()
        review_reason = collision_reason or Trial._review_reason(product, email, domain, db_uuid, ip)
        trial = Trial.create({
            'product_id': product.id,
            'contact_name': _text(kw.get('contact_name')).strip()[:100] or email,
            'contact_email': email,
            'contact_phone': _text(kw.get('contact_phone')).strip()[:50],
            'company_name': company[:100],
            'company_size': kw.get('company_size') if kw.get('company_size') in (
                'solo', '2-10', '11-50', '51-250', '251+',
            ) else False,
            'industry': _text(kw.get('industry')).strip()[:100],
            'domain': domain,
            'db_uuid': db_uuid or False,
            'ip_address': ip,
            'consent_at': fields.Datetime.now(),
            'consent_version': privacy_version,
            'expected_use_case': _text(kw.get('expected_use_case')).strip()[:1000],
            'review_reason': review_reason or False,
        })

        if review_reason:
            _logger.info("[wb_subscription] Trial %s zur Prüfung: %s", trial.name, review_reason)
            return trial._response()

        try:
            with request.env.cr.savepoint():
                trial.action_approve_and_create_trial()
        except Exception as e:
            _logger.exception("[wb_subscription] Trial-Auto-Approve fehlgeschlagen: %s", e)
            trial.write({'review_reason': 'Automatische Freigabe fehlgeschlagen'})
            return {'error': 'TRIAL_PROCESSING_FAILED'}

        return trial._response()

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
        # Konstantzeitiger Vergleich (#97, I7)
        if not hmac.compare_digest(api_key_header.encode('utf-8'), configured_key.encode('utf-8')):
            return {'error': 'UNAUTHORIZED'}

        email = _text(kw.get('contact_email')).strip().lower()
        if not EMAIL_RE.match(email):
            return {'error': 'INVALID_EMAIL'}

        # Variante ausdrücklich: product_id oder default_code. Ohne Angabe nur,
        # wenn das Produkt genau eine Variante hat (#97, I7).
        product_code = _text(kw.get('product_code')).strip().upper()
        candidates = request.env['product.product'].sudo().search([
            ('wb_technical_code', '=', product_code),
            ('wb_is_license_product', '=', True),
        ])
        product_id = kw.get('product_id')
        default_code = _text(kw.get('default_code')).strip()
        if isinstance(product_id, int) and not isinstance(product_id, bool):
            product = candidates.filtered(lambda p: p.id == product_id)
        elif default_code:
            product = candidates.filtered(lambda p: p.default_code == default_code)
        elif len(candidates) > 1:
            return {'error': 'AMBIGUOUS_PRODUCT'}
        else:
            product = candidates
        if len(product) != 1:
            return {'error': 'PRODUCT_NOT_FOUND'}

        # Exakter Vergleich der normalisierten Adresse, keine LIKE-Muster (#97, I1)
        partner = request.env['res.partner'].sudo().search([
            ('email_normalized', '=', email),
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
