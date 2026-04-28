"""WB License Client — Service-Klasse für Kunden-Odoos.

Der zentrale Einstiegspunkt für Produkt-Module. Kümmert sich um:
- Key-Speicherung in ir.config_parameter (verschlüsselt-ähnlich durch ACL-Schutz)
- Täglicher Ping an den WB-Server (/api/license/check)
- Offline-toleranter Status-Cache (wb.license.info, 30 Tage Grace)
- Erstaktivierung via /api/license/activate
- Migration-Request via /api/license/migrate

Decorator license_required ist unten im Modul definiert und kann so
importiert werden:

    from odoo.addons.wb_license_client.models.wb_license_client import license_required

    @license_required('TELE')
    def action_send_sms(self):
        ...

    @license_required('TELE', min_cache_age_days=7)
    def action_send_bulk_sms(self):      # externe Kosten -> strenger
        ...
"""

import logging
import random
from datetime import timedelta
from functools import wraps

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


DEFAULT_SERVER_URL = 'https://wissen-beratung.de'
KEY_PARAM_PREFIX = 'wb_license_client.key_'
SERVER_URL_PARAM = 'wb_license_client.server_url'
DEBUG_PARAM = 'wb_license_client.debug_mode'
INSTALL_REGISTRY_OPT_OUT_PARAM = 'wb_license_client.disable_install_registry'
DEFAULT_CHECK_TIMEOUT = 10
DEFAULT_ACTIVATE_TIMEOUT = 15
DEFAULT_ANNOUNCE_TIMEOUT = 5
DEFAULT_MIN_CACHE_AGE_DAYS = 30
INSTALL_ANNOUNCE_THROTTLE_HOURS = 24


class WbLicenseClient(models.AbstractModel):
    _name = 'wb.license.client'
    _description = 'WB License Client Service'

    @api.model
    def check_license(self, product_code, min_cache_age_days=DEFAULT_MIN_CACHE_AGE_DAYS):
        """Hauptmethode. Liefert wb.license.info-Record mit aktuellem Status.

        Flow (siehe docs/modules/wb_license_client.md):
          1. Key aus ir.config_parameter lesen
          2. Cache-Eintrag (wb.license.info) prüfen
          3. Wenn Cache < 24h alt: Cache zurückgeben (kein HTTP-Call)
          4. Sonst: Ping an Server, Cache aktualisieren
          5. Bei Server-Fehler: alten Cache behalten, state bleibt wie es war
          6. is_valid berücksichtigt min_cache_age_days für 'unknown'-State

        :param product_code: 4-stelliger Produkt-Code (z.B. 'TELE')
        :param min_cache_age_days: Max. Alter der letzten erfolgreichen
            Prüfung für 'unknown'-State. Default 30. Strenger bei Methoden
            mit externen Kosten.
        :return: wb.license.info Record (erstellt einen, wenn noch keiner existiert)
        """
        info = self._get_or_create_info(product_code)
        key = self._get_stored_key(product_code)

        if not key:
            info.write({'state': 'unlicensed', 'key': False})
            self._maybe_announce_install(info, product_code)
            return info

        needs_refresh = (
            not info.last_server_check
            or info.last_server_check < fields.Datetime.now() - timedelta(hours=24)
        )
        if needs_refresh:
            self._do_ping(info, product_code, key)

        info.apply_cache_age_policy(min_cache_age_days)
        return info

    @api.model
    def activate_license(self, product_code, key, activation_code):
        """Ruft /api/license/activate auf dem Server auf.

        Bei Erfolg: Key wird in ir.config_parameter gespeichert, Cache
        aktualisiert. Bei Fehler: UserError mit aussagekräftiger Meldung.

        Wird vom Activate-Wizard aufgerufen.
        """
        payload = {
            'key': key,
            'activation_code': activation_code,
            'domain': self._get_domain(),
            'db_uuid': self._get_db_uuid(),
            'email': self.env.user.email or '',
            'client_version': self._get_module_version(),
        }
        data, status = self._do_request(
            '/api/license/activate', payload, timeout=DEFAULT_ACTIVATE_TIMEOUT,
        )

        if status == 200 and data and data.get('status') == 'ok':
            self._store_key(product_code, key)
            info = self._get_or_create_info(product_code)
            info.write({
                'key': key,
                'state': 'active',
                'valid_to': data.get('valid_to'),
                'last_server_check': fields.Datetime.now(),
                'last_check_success': True,
                'last_error_message': False,
            })
            return info

        self._raise_activation_error(status, data)

    @api.model
    def request_migration(self, product_code, new_domain, new_db_uuid, reason, contact_email):
        """Ruft /api/license/migrate auf dem Server auf."""
        key = self._get_stored_key(product_code)
        if not key:
            raise UserError(_(
                "Keine Lizenz für Produkt '%s' konfiguriert — Migration nicht möglich."
            ) % product_code)
        payload = {
            'key': key,
            'current_domain': self._get_domain(),
            'current_db_uuid': self._get_db_uuid(),
            'new_domain': new_domain,
            'new_db_uuid': new_db_uuid,
            'reason': reason,
            'contact_email': contact_email,
        }
        data, status = self._do_request('/api/license/migrate', payload)
        if status != 200:
            raise UserError(_(
                "Migration konnte nicht beantragt werden (HTTP %s). Bitte später erneut versuchen "
                "oder support@wissen-beratung.de kontaktieren."
            ) % status)
        return data

    @api.model
    def register_install(self, product_code):
        """Meldet diese Odoo-Instanz als Install bei wissen-beratung.de an.

        Wird aus zwei Pfaden gerufen:
        1. ``_post_init_hook`` von Produkt-Modulen — explizit beim Install.
        2. ``check_license`` — Fallback wenn (noch) kein Key gespeichert ist
           und der letzte Announce > 24h zurückliegt.

        Best-effort: Fehler werden geloggt, aber nie geworfen — ein
        unerreichbarer Lizenz-Server darf den Module-Install nicht crashen.
        Opt-out via ``ir.config_parameter`` ``wb_license_client.disable_install_registry=True``.

        :param product_code: 4-Char Produkt-Code (z.B. 'TELE')
        :return: True wenn Announce erfolgreich, False sonst
        """
        if self._install_registry_disabled():
            return False

        info = self._get_or_create_info(product_code)
        return self._announce_install(info, product_code)

    @api.model
    def _maybe_announce_install(self, info, product_code):
        """Throttled Announce-Call aus check_license heraus.

        Nur einmal pro 24h pro (product_code, company), damit der täglich
        polling check_license keinen Hammer auf den Server wirft.
        """
        if self._install_registry_disabled():
            return False
        last = info.last_install_announce_at
        if last and last > fields.Datetime.now() - timedelta(
                hours=INSTALL_ANNOUNCE_THROTTLE_HOURS):
            return False
        return self._announce_install(info, product_code)

    @api.model
    def _announce_install(self, info, product_code):
        """Internal HTTP-Call /api/license/announce. Best-effort."""
        payload = {
            'product_code': product_code,
            'domain': self._get_domain(),
            'db_uuid': self._get_db_uuid(),
            'client_version': self._get_module_version(),
            'email': self.env.user.email or '',
        }
        if not payload['domain'] or not payload['db_uuid']:
            _logger.info(
                "[wb_license_client] Announce für %s übersprungen — "
                "keine domain/db_uuid verfügbar.", product_code)
            return False
        try:
            data, status = self._do_request(
                '/api/license/announce', payload, timeout=DEFAULT_ANNOUNCE_TIMEOUT,
            )
        except Exception as e:
            _logger.warning(
                "[wb_license_client] Announce für %s fehlgeschlagen: %s",
                product_code, e)
            return False
        if status == 200 and data and data.get('status') == 'ok':
            info.sudo().write({'last_install_announce_at': fields.Datetime.now()})
            return True
        _logger.info(
            "[wb_license_client] Announce für %s lief durch, Server-Antwort: "
            "status=%s data=%s", product_code, status, data)
        return False

    @api.model
    def _install_registry_disabled(self):
        param = self.env['ir.config_parameter'].sudo().get_param(
            INSTALL_REGISTRY_OPT_OUT_PARAM)
        return str(param).lower() in ('1', 'true', 'yes')

    @api.model
    def _get_or_create_info(self, product_code):
        """Findet oder erzeugt den wb.license.info-Record für (product, company)."""
        Info = self.env['wb.license.info'].sudo()
        info = Info.search([
            ('product_code', '=', product_code),
            ('company_id', '=', self.env.company.id),
        ], limit=1)
        if not info:
            info = Info.create({
                'product_code': product_code,
                'company_id': self.env.company.id,
                'state': 'unlicensed',
            })
        return info

    @api.model
    def _do_ping(self, info, product_code, key):
        """Internal HTTP-Call /api/license/check."""
        payload = {
            'key': key,
            'product_code': product_code,
            'domain': self._get_domain(),
            'db_uuid': self._get_db_uuid(),
            'client_version': self._get_module_version(),
        }
        data, status = self._do_request('/api/license/check', payload)
        if status == 200 and data:
            info.apply_server_response(data)
        else:
            info.record_check_failure(status, data)

    @api.model
    def _do_request(self, endpoint, payload, timeout=DEFAULT_CHECK_TIMEOUT):
        """HTTP-POST mit Timeout und Error-Handling.

        Returns:
            (response_json_or_None, status_code_or_0)
            status=0 signalisiert Netzwerk-Fehler (Timeout/ConnectionError).
        """
        url = self._get_server_url() + endpoint
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': f'wb_license_client/{self._get_module_version()}',
            'X-WB-DB-UUID': self._get_db_uuid() or '',
            'X-WB-Domain': self._get_domain() or '',
        }
        try:
            envelope = {'jsonrpc': '2.0', 'method': 'call', 'params': payload}
            response = requests.post(url, json=envelope, headers=headers, timeout=timeout)
            try:
                data = response.json()
            except ValueError:
                data = None
            if isinstance(data, dict) and 'result' in data:
                data = data['result']
            return data, response.status_code
        except requests.Timeout:
            _logger.warning("[wb_license_client] Timeout beim Call %s", endpoint)
            return None, 0
        except requests.ConnectionError as e:
            _logger.warning("[wb_license_client] Connection error %s: %s", endpoint, e)
            return None, 0
        except requests.RequestException as e:
            _logger.exception("[wb_license_client] HTTP error %s: %s", endpoint, e)
            return None, 0

    @api.model
    def _raise_activation_error(self, status, data):
        """Übersetzt Server-Error-Codes in User-freundliche Meldungen."""
        error_code = (data or {}).get('error') if data else None
        messages = {
            'INVALID_KEY_FORMAT': _(
                "Der Lizenzschlüssel hat ein ungültiges Format. Bitte genau so eingeben "
                "wie er auf dem Zertifikat steht."),
            'INVALID_CODE_FORMAT': _(
                "Der Aktivierungs-Code hat ein ungültiges Format. 5 Gruppen à 5 Zeichen, "
                "mit Bindestrichen getrennt."),
            'KEY_NOT_FOUND': _(
                "Dieser Lizenzschlüssel ist nicht bekannt. Bitte Eingabe prüfen oder "
                "support@wissen-beratung.de kontaktieren."),
            'WRONG_CODE': _(
                "Der Aktivierungs-Code ist falsch. Bitte prüfen Sie den Code im Portal "
                "und versuchen Sie es erneut."),
            'ALREADY_ACTIVATED': _(
                "Dieser Lizenzschlüssel wurde bereits an einer anderen Instanz aktiviert. "
                "Möchten Sie eine Migration beantragen?"),
            'ACTIVATION_EXPIRED': _(
                "Der Aktivierungs-Zeitraum ist abgelaufen. Bitte kontaktieren Sie "
                "support@wissen-beratung.de für eine Reaktivierung."),
            'TOO_MANY_ATTEMPTS': _(
                "Zu viele Fehlversuche. Bitte warten Sie 1 Stunde und versuchen Sie es erneut."),
        }
        message = messages.get(error_code)
        if not message:
            if status == 0:
                message = _(
                    "Der Lizenz-Server ist nicht erreichbar. Bitte prüfen Sie Ihre "
                    "Internetverbindung und versuchen Sie es erneut.")
            else:
                message = _(
                    "Aktivierung fehlgeschlagen (HTTP %s). Bitte support@wissen-beratung.de "
                    "kontaktieren.") % status
        raise UserError(message)

    @api.model
    def _get_stored_key(self, product_code):
        return self.env['ir.config_parameter'].sudo().get_param(
            KEY_PARAM_PREFIX + product_code) or False

    @api.model
    def _store_key(self, product_code, key):
        self.env['ir.config_parameter'].sudo().set_param(
            KEY_PARAM_PREFIX + product_code, key)

    @api.model
    def _get_server_url(self):
        return self.env['ir.config_parameter'].sudo().get_param(
            SERVER_URL_PARAM, DEFAULT_SERVER_URL).rstrip('/')

    @api.model
    def _get_db_uuid(self):
        return self.env['ir.config_parameter'].sudo().get_param('database.uuid')

    @api.model
    def _get_domain(self):
        return self.env['ir.config_parameter'].sudo().get_param('web.base.url')

    @api.model
    def _get_module_version(self):
        module = self.env['ir.module.module'].sudo().search(
            [('name', '=', 'wb_license_client')], limit=1)
        return module.latest_version or '19.0.1.0.0'

    @api.model
    def _get_all_configured_product_codes(self):
        """Findet alle Product-Codes die in ir.config_parameter einen Key haben."""
        params = self.env['ir.config_parameter'].sudo().search([
            ('key', '=like', KEY_PARAM_PREFIX + '%'),
        ])
        return [p.key[len(KEY_PARAM_PREFIX):] for p in params if p.value]

    @api.model
    def _cron_daily_ping(self):
        """Täglicher Cron: pingt alle konfigurierten Lizenzen.

        Nach jedem Ping wird nextcall auf heute + 24h + random(0..4h)
        gesetzt, damit nicht alle Kunden um 01:00 gleichzeitig den
        Server überrennen.
        """
        codes = self._get_all_configured_product_codes()
        for code in codes:
            try:
                key = self._get_stored_key(code)
                if key:
                    info = self._get_or_create_info(code)
                    self._do_ping(info, code, key)
            except Exception as e:
                _logger.exception("[wb_license_client] Ping for %s failed: %s", code, e)

        cron = self.env.ref('wb_license_client.ir_cron_daily_ping', raise_if_not_found=False)
        if cron:
            jitter_hours = random.uniform(0, 4)
            cron.sudo().nextcall = fields.Datetime.now() + timedelta(
                hours=24 + jitter_hours,
            )


def license_required(product_code, min_cache_age_days=DEFAULT_MIN_CACHE_AGE_DAYS):
    """Decorator für Methoden die eine gültige Lizenz erfordern.

    Standardmäßig (min_cache_age_days=30) wird bei 'unknown'-State bis zu
    30 Tage Server-Offline toleriert — Schutz vor falsch-positivem
    Blockieren bei WB-Server-Ausfall.

    Für Methoden mit externen Kosten (z.B. Telnyx-SMS-Send) sollte ein
    kürzerer min_cache_age_days gesetzt werden, z.B. 7.

    Usage:
        @license_required('TELE')
        def action_send_sms(self):
            ...

        @license_required('TELE', min_cache_age_days=7)
        def action_send_bulk_sms(self):
            ...
    """
    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            info = self.env['wb.license.client'].check_license(
                product_code, min_cache_age_days=min_cache_age_days,
            )
            if not info.is_valid:
                raise UserError(info.user_message or _(
                    "Keine gültige Lizenz für '%s'. Bitte unter Einstellungen "
                    "→ WB Lizenzen aktivieren."
                ) % product_code)
            return func(self, *args, **kwargs)
        return wrapper
    return decorator
