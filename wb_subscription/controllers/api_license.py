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

import psycopg2

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

        Akzeptiert optional ``module_version`` (Version des Produkt-Moduls,
        z. B. '19.0.1.1.0' aus wb_bitwarden_pro/__manifest__.py). Wird in
        wb.license.install gespeichert und mit der gepflegten
        product.template.wb_latest_module_version verglichen — bei
        verfügbarem Update gibt die Response ``latest_module_version`` und
        ``download_url`` zurück, damit der Client einen Banner anzeigen
        kann. Reines Datenfeld, keine automatische Auslieferung.

        Rate-Limit (Sprint 3 / L-C2): zwei Achsen.
        * pro IP: 100/h (gegen breites Scanning)
        * pro Key: 200/h (gegen Multi-IP-Scanning desselben Keys —
          legitimer Cron 24x/Tag, da reicht 200 Headroom für Multi-Tenant)
        """
        if not _check_rate_limit(_client_ip(), 'check', 100, 3600):
            return {'error': 'TOO_MANY_REQUESTS'}

        key = (kw.get('key') or '').strip()
        gen = request.env['wb.key.generator'].sudo()
        if not gen.validate_key_format(key):
            return {'error': 'INVALID_KEY_FORMAT'}

        # Per-Key Rate-Limit nach Format-Validation, damit Spam-Versuche
        # mit Junk-Strings nicht den per-Key-Counter belasten.
        if not _check_rate_limit('key:' + key, 'check_key', 200, 3600):
            return {'error': 'TOO_MANY_REQUESTS'}

        license = request.env['wb.license.key'].sudo().search([('name', '=', key)], limit=1)
        if not license:
            return {'error': 'KEY_NOT_FOUND'}

        license.record_ping(ip=_client_ip(), user_agent=_client_ua())
        request.env['wb.license.event'].sudo().log_event(
            license, 'ping',
            ip_address=_client_ip(), user_agent=_client_ua(),
            domain=kw.get('domain'), db_uuid=kw.get('db_uuid'),
        )

        domain = (kw.get('domain') or '').strip()
        db_uuid = (kw.get('db_uuid') or '').strip()
        module_version = (kw.get('module_version') or '').strip()
        if module_version and license.product_code and domain and db_uuid:
            install = request.env['wb.license.install'].sudo().search([
                ('product_code', '=', license.product_code),
                ('domain', '=', domain),
                ('db_uuid', '=', db_uuid),
            ], limit=1)
            if install and install.installed_module_version != module_version:
                install.write({'installed_module_version': module_version})

        product_tmpl = license.product_id.product_tmpl_id
        latest_module_version = (
            product_tmpl.wb_latest_module_version or ''
        ).strip() or None
        download_url = None
        if latest_module_version and latest_module_version != module_version:
            if product_tmpl.wb_has_downloadable_tarball:
                base_url = (request.env['ir.config_parameter'].sudo()
                            .get_param('web.base.url') or '').rstrip('/')
                if base_url:
                    download_url = f'{base_url}/my/downloads/{license.id}'
            if not download_url:
                download_url = request.env['ir.config_parameter'].sudo().get_param(
                    'wb_subscription.download_portal_url') or None

        return {
            'state': license.state,
            'valid_from': license.valid_from.isoformat() if license.valid_from else None,
            'valid_to': license.valid_to.isoformat() if license.valid_to else None,
            'grace_until': license.grace_until.isoformat() if license.grace_until else None,
            'product_code': license.product_code,
            'latest_module_version': latest_module_version,
            'download_url': download_url,
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
        * ``request_id`` — UUID4 vom Client (Sprint 3 / L-C1). Bei
          identischer ID innerhalb 1h wird das gespeicherte Resultat
          zurückgegeben — schützt gegen DB-Clone-Replay und
          Doppelklick-Aktivierungen.

        Rate-Limit (Sprint 3 / L-C2): zwei Achsen.
        * pro IP: 5/h
        * pro Key: 5/h — verhindert dass ein Angreifer mit 100 IPs gegen
          100 verschiedene Keys parallel scannt.
        """
        if not _check_rate_limit(_client_ip(), 'activate', 5, 3600):
            return {'error': 'TOO_MANY_ATTEMPTS'}

        key = (kw.get('key') or '').strip()
        code = (kw.get('activation_code') or '').strip()
        domain = (kw.get('domain') or '').strip()
        db_uuid = (kw.get('db_uuid') or '').strip()
        contact_email = (kw.get('contact_email') or kw.get('email') or '').strip()
        request_id = (kw.get('request_id') or '').strip()

        gen = request.env['wb.key.generator'].sudo()
        if not gen.validate_key_format(key):
            return {'error': 'INVALID_KEY_FORMAT'}
        if not gen.validate_activation_code_format(code):
            return {'error': 'INVALID_CODE_FORMAT'}
        if not domain or not db_uuid:
            return {'error': 'MISSING_BINDING_DATA'}

        # Per-Key Rate-Limit nach Format-Validation
        if not _check_rate_limit('key:' + key, 'activate_key', 5, 3600):
            return {'error': 'TOO_MANY_ATTEMPTS'}

        # Sprint 3 / L-C1 — Replay-Schutz via request_id-Dedup.
        # Wenn der Client eine request_id schickt: in der Dedup-Tabelle
        # nachsehen ob wir das schon mal verarbeitet haben. Wenn ja:
        # gespeichertes Resultat zurueckgeben (idempotent fuer
        # Doppelklick + Sicherung gegen DB-Clone-Replay).
        # Wenn keine request_id (alter Client): Fallback auf
        # (key, db_uuid, domain, hour-Bucket) — verhindert dass eine
        # geclonte DB den selben Key innerhalb derselben Stunde nochmal
        # aktiviert.
        ActivationReq = request.env['wb.license.activation_request'].sudo()
        cached = ActivationReq.find_replay(
            request_id=request_id or None,
            key=key, domain=domain, db_uuid=db_uuid,
        )
        if cached is not None:
            return cached

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
            payload = {
                'status': 'ok',
                'state': license.state,
                'bound_domain': license.bound_domain,
                'valid_to': license.valid_to.isoformat() if license.valid_to else None,
            }
        else:
            payload = result

        # Sprint 3 / L-C1: Resultat im Dedup-Store ablegen — fuer Replay
        # innerhalb des TTL-Fensters wird genau dieses Payload returned.
        ActivationReq.record_result(
            request_id=request_id or None,
            key=key, domain=domain, db_uuid=db_uuid,
            payload=payload,
        )
        return payload

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
        except (psycopg2.IntegrityError, psycopg2.OperationalError):
            # Sprint 5 / L-M3: DB-Konsistenzfehler nicht silently
            # schlucken — der Aufrufer muss erfahren, dass etwas
            # schiefläuft. Re-raise propagiert zum Odoo-RPC-Layer.
            _logger.exception(
                "[wb_subscription] Lead-Event-Log DB-Fehler — re-raise")
            raise
        except Exception as exc:
            # Logging-Layer-Fehler (Mail-Render, Selection-Mismatch ...)
            # bleiben silent — wir wollen den Lead-Endpoint nicht wegen
            # einem schiefgegangenen Audit-Eintrag killen.
            _logger.warning("[wb_subscription] lead event-log failed: %s", exc)

        return {
            'status': 'ok',
            'install_id': install.id,
            'lead_id': install.crm_lead_id.id if install.crm_lead_id else False,
            'intent': intent,
        }

    @http.route('/api/license/lookup',
                type='json', auth='public', methods=['POST'],
                csrf=False, cors=CORS_ANY)
    def lookup_license(self, **kw):
        """Zero-Touch-Aktivierung: Auto-Bind einer armierten Lizenz.

        Wird vom wb_license_client beim Modul-Install aufgerufen, um eine
        Lizenz zu finden und automatisch zu binden (ohne dass der Kunde
        Key+Code manuell eintippen muss). Voraussetzung: WB hat die Lizenz
        beim Verkauf vorbelegt (pre_assigned_domain/email) und armiert
        (auto_bind_armed=True, auto_bind_armed_until in der Zukunft).

        Request:
            {
                "product_code": "BITW",
                "db_uuid": "abc-...",
                "domain": "https://kunde.odoo.com",
                "email": "kunde@example.com"   # Fallback wenn Domain unklar
            }

        Response (Match):
            {"found": true, "key": "WB-BITW-...", "valid_to": "...",
             "state": "active"}

        Response (No-Match): {"found": false}

        Rate-Limit: 10/h pro IP — strikt, weil Match-Antwort einen
        gueltigen Key + Activation enthaelt. Anti-Enumeration.
        """
        if not _check_rate_limit(_client_ip(), 'lookup', 10, 3600):
            return {'error': 'TOO_MANY_REQUESTS'}

        product_code = (kw.get('product_code') or '').strip().upper()
        db_uuid = (kw.get('db_uuid') or '').strip()
        domain = (kw.get('domain') or '').strip()
        email = (kw.get('email') or '').strip()

        if not product_code or len(product_code) != 4:
            return {'error': 'INVALID_PRODUCT_CODE'}
        if not db_uuid:
            return {'error': 'MISSING_BINDING_DATA'}
        if not domain and not email:
            return {'error': 'MISSING_MATCH_CRITERIA'}

        License = request.env['wb.license.key'].sudo()
        license = License._lookup_for_auto_bind(
            product_code, db_uuid, domain, email,
        )
        if not license:
            request.env['wb.license.event'].sudo().log_event(
                False, 'auto_bind_lookup_failed',
                ip_address=_client_ip(), user_agent=_client_ua(),
                domain=domain, db_uuid=db_uuid,
                details={'product_code': product_code,
                         'email': email or ''},
            )
            return {'found': False}

        license._bind_via_auto_lookup(
            db_uuid, domain, email,
            ip=_client_ip(), user_agent=_client_ua(),
        )
        return {
            'found': True,
            'key': license.name,
            'state': license.state,
            'valid_from': license.valid_from.isoformat() if license.valid_from else None,
            'valid_to': license.valid_to.isoformat() if license.valid_to else None,
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
