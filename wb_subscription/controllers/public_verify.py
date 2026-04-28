"""Öffentliche Lizenz-Verifikations-Page.

Ziel der QR-Codes auf den Zertifikaten. Anyone kann hier prüfen ob
ein Lizenzschlüssel gültig ist.

Sicherheits-Eigenschaften:
- reCAPTCHA v3 gegen Bot-Scraping (DECISION #43)
- Rate-Limit 20/h pro IP gegen Enumeration
- Kunde wird anonymisiert dargestellt (z.B. "M.... GmbH")
- Kein bound_domain, keine bound_db_uuid, keine Email
- Keine Activation-Codes (DECISION #39)
"""

import logging

import requests

from odoo import http, _
from odoo.http import request

_logger = logging.getLogger(__name__)

RECAPTCHA_VERIFY_URL = 'https://www.google.com/recaptcha/api/siteverify'
RECAPTCHA_MIN_SCORE = 0.5
RECAPTCHA_TIMEOUT = 5


def _client_ip():
    return request.httprequest.remote_addr or '0.0.0.0'


def _rate_limit_check():
    return request.env['wb.rate.limit.entry'].sudo().check_and_increment(
        f"verify:{_client_ip()}", 'verify', 20, 3600,
    )


def _get_recaptcha_keys():
    icp = request.env['ir.config_parameter'].sudo()
    site_key = icp.get_param('wb_subscription.recaptcha_site_key') or ''
    secret_key = icp.get_param('wb_subscription.recaptcha_secret_key') or ''
    return site_key, secret_key


def _verify_recaptcha_token(token, secret_key):
    """Validiert reCAPTCHA-Token gegen Google. Returns (valid, score)."""
    if not secret_key or not token:
        return False, 0.0
    try:
        response = requests.post(
            RECAPTCHA_VERIFY_URL,
            data={
                'secret': secret_key,
                'response': token,
                'remoteip': _client_ip(),
            },
            timeout=RECAPTCHA_TIMEOUT,
        )
        result = response.json()
        return bool(result.get('success')), float(result.get('score', 0.0))
    except (requests.RequestException, ValueError) as e:
        _logger.warning("[wb_subscription] reCAPTCHA-Verify fehlgeschlagen: %s", e)
        return False, 0.0


# Sprint 3 / L-C3 — Rechtsform-Suffixe werden vor dem Masken-Print
# entfernt, damit kein Reverse-Lookup gegen Handelsregister möglich ist.
# Set ist case-insensitive, ohne Punkte und Klammern; ``_is_legal_suffix``
# normalisiert das eingehende Token entsprechend.
_LEGAL_SUFFIX_TOKENS = frozenset({
    # Deutschland
    'gmbh', 'mbh', 'ag', 'ug', 'kg', 'ohg', 'ek', 'ev',
    'haftungsbeschränkt', 'haftungsbeschraenkt',
    # Konjunktion in Co.-Konstrukten
    'co', '&',
    # UK / US
    'ltd', 'limited', 'llc',
    'inc', 'corp', 'corporation', 'plc', 'lp', 'llp',
    # CH / FR / ES / IT
    'sa', 'spa', 'sas', 'sarl', 'srl',
    # NL / SE / NO / DK / FI / AT
    'bv', 'ab', 'as', 'aps', 'oy', 'gesmbh',
    # AU
    'pty',
})


def _is_legal_suffix(token):
    """True wenn ``token`` eine bekannte Rechtsform ist (case-insensitive,
    Punkte und Klammern werden vor dem Vergleich entfernt)."""
    if not token:
        return False
    normalized = token.lower().strip('.,()').replace('.', '')
    return normalized in _LEGAL_SUFFIX_TOKENS


def _anonymize_partner_name(name):
    """Liefert nur den ersten Buchstaben des eigentlichen Firmennamens
    plus Ellipsis. Rechtsform-Suffixe und Längen-Information werden
    entfernt (Sprint 3 / L-C3, gegen Reverse-Lookup).

    Beispiele:
        'Müller GmbH'                 → 'M…'
        'Müller-Schmidt KG'           → 'M…'
        'Acme Holdings GmbH & Co. KG' → 'A…'
        'Wissen Beratung'             → 'W…'
        'Müller'                      → 'M…'
        ''                            → ''
    """
    if not name:
        return ''
    cleaned = name.replace(',', ' ').strip()
    if not cleaned:
        return ''
    tokens = cleaned.split()
    while tokens and _is_legal_suffix(tokens[-1]):
        tokens.pop()
    if not tokens:
        return '***'
    first = tokens[0]
    return f'{first[0]}…'


def _build_verify_data(license):
    """Baut das öffentlich angezeigte Verifikations-Dict.

    NIEMALS bound_domain, bound_db_uuid, partner.email, partner.vat,
    activation_*, oder full partner_name aufnehmen.
    """
    return {
        'key': license.name,
        'state': license.state,
        'state_label': dict(license._fields['state'].selection).get(license.state, license.state),
        'product_name': license.product_id.name,
        'partner_anonymous': _anonymize_partner_name(license.partner_id.name or ''),
        'valid_from': license.valid_from.isoformat() if license.valid_from else None,
        'valid_to': license.valid_to.isoformat() if license.valid_to else None,
        'is_valid': license.state in ('active', 'grace', 'trial'),
        'certificate_number': license.certificate_number or None,
    }


class PublicVerifyController(http.Controller):

    @http.route('/license/verify/<string:key>',
                type='http', auth='public', methods=['GET'], csrf=False)
    def verify_landing(self, key, **kw):
        """Initiale Verify-Page mit reCAPTCHA-Integration.

        Wenn reCAPTCHA konfiguriert: Page lädt mit reCAPTCHA-v3-Script,
        JS schickt Token an /check-Endpoint und ersetzt den Inhalt mit
        den Lizenz-Daten.

        Wenn reCAPTCHA NICHT konfiguriert: Page zeigt Daten direkt
        (mit Logger-Warning — sollte in Produktion nicht passieren).
        """
        if not _rate_limit_check():
            return request.render(
                'wb_subscription.public_verify_rate_limited',
                {'key': key},
            )

        license = request.env['wb.license.key'].sudo().search(
            [('name', '=', key)], limit=1)
        if not license:
            return request.render(
                'wb_subscription.public_verify_not_found',
                {'key': key},
            )

        site_key, secret_key = _get_recaptcha_keys()
        if not site_key or not secret_key:
            _logger.warning(
                "[wb_subscription] /license/verify aufgerufen, aber "
                "wb_subscription.recaptcha_site_key/secret_key nicht konfiguriert. "
                "Page zeigt Daten OHNE Captcha-Schutz — bitte Keys einrichten!")
            return request.render(
                'wb_subscription.public_verify_result',
                _build_verify_data(license),
            )

        return request.render(
            'wb_subscription.public_verify_landing',
            {
                'key': key,
                'recaptcha_site_key': site_key,
            },
        )

    @http.route('/license/verify/<string:key>/check',
                type='json', auth='public', methods=['POST'], csrf=False)
    def verify_check(self, key, **kw):
        """JSON-Endpoint: prüft reCAPTCHA und gibt Lizenz-Daten zurück."""
        if not _rate_limit_check():
            return {'error': 'TOO_MANY_REQUESTS'}

        token = (kw.get('recaptcha_token') or '').strip()
        _site_key, secret_key = _get_recaptcha_keys()
        if secret_key:
            valid, score = _verify_recaptcha_token(token, secret_key)
            if not valid or score < RECAPTCHA_MIN_SCORE:
                return {'error': 'CAPTCHA_FAILED', 'score': score}

        license = request.env['wb.license.key'].sudo().search(
            [('name', '=', key)], limit=1)
        if not license:
            return {'error': 'KEY_NOT_FOUND'}

        return {'status': 'ok', 'data': _build_verify_data(license)}
