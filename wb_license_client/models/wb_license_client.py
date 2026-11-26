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

import hashlib
import hmac
import json
import logging
import random
import re
import secrets
import time
import uuid
from datetime import timedelta
from functools import wraps

import requests

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .wb_license_info import parse_max_users

_logger = logging.getLogger(__name__)


DEFAULT_SERVER_URL = 'https://my.wissen-beratung.de'
KEY_PARAM_PREFIX = 'wb_license_client.key_'
SECRET_PARAM_PREFIX = 'wb_license_client.secret_'
SIGNATURE_SCHEME = 'WB-HMAC-V1'
SERVER_URL_PARAM = 'wb_license_client.server_url'
SERVER_DATABASE_PARAM = 'wb_license_client.server_database'
DEBUG_PARAM = 'wb_license_client.debug_mode'
INSTALL_REGISTRY_OPT_OUT_PARAM = 'wb_license_client.disable_install_registry'
DEFAULT_CHECK_TIMEOUT = 10
DEFAULT_ACTIVATE_TIMEOUT = 15
DEFAULT_TRIAL_TIMEOUT = 20
# Fassung der Datenschutzhinweise, der beim Test zugestimmt wird (Nachweis am Server, #97)
PRIVACY_VERSION = '2026-11-26'
# Format eines Public Keys (wie wb.key.generator auf dem Server, ohne Prüfsumme).
KEY_FORMAT_RE = re.compile(r'^WB-([A-Z0-9]{4})-([a-f0-9]{8})([A-Z2-7]{2})$')
DEFAULT_ANNOUNCE_TIMEOUT = 5
DEFAULT_MIN_CACHE_AGE_DAYS = 30
INSTALL_ANNOUNCE_THROTTLE_HOURS = 24
# Nach einem fehlgeschlagenen Ping frühestens nach dieser Zeit erneut pingen;
# bis dahin gilt der gespeicherte Zustand (min_cache_age_days unverändert).
PING_RETRY_MINUTES = 15

# Zusätzliche Ping-Pause im Arbeitsspeicher je Worker: {(dbname, product_code):
# time.monotonic() des letzten Fehlschlags}. Der DB-Merker ``last_failed_check_at``
# geht verloren, wenn die Transaktion der gegateten Methode zurückgerollt wird;
# dieser Eintrag bleibt. Bewusst ohne Lock und ohne zweiten Cursor (der würde
# sich an derselben Zeile selbst blockieren). Ein Dict-Zugriff genügt je Worker.
_PING_SPERRE = {}


class WbLicenseClient(models.AbstractModel):
    _name = 'wb.license.client'
    _description = 'WB License Client Service'

    # ------------------------------------------------------------------
    # Zugriff: Die öffentlichen Methoden dieses Dienstes sind nicht per RPC
    # aufrufbar (@api.private), schreibende zusätzlich nur für Administratoren
    # (DECISIONS.md #97). Produktmodule rufen sie serverseitig auf.
    # ------------------------------------------------------------------

    @api.model
    def _ensure_admin(self):
        """Raise AccessError unless the caller is superuser or a settings administrator."""
        if not self.env.is_superuser() and not self.env.user.has_group('base.group_system'):
            raise AccessError(_("Only administrators can manage WB licenses."))

    @api.model
    def _key_matches(self, product_code, key):
        """True if ``key`` is a well-formed key of ``product_code`` (prefix WB-<code>-)."""
        match = KEY_FORMAT_RE.match((key or '').strip())
        return bool(match and match.group(1) == (product_code or '').strip().upper())

    @api.private
    @api.model
    def check_license(self, product_code, min_cache_age_days=DEFAULT_MIN_CACHE_AGE_DAYS,
                      module_name=None):
        """Server-side entry point for product modules (not callable over RPC)."""
        return self._check_license(product_code, min_cache_age_days=min_cache_age_days,
                                   module_name=module_name)

    @api.model
    def _check_license(self, product_code, min_cache_age_days=DEFAULT_MIN_CACHE_AGE_DAYS,
                       module_name=None):
        """Hauptmethode. Liefert wb.license.info-Record mit aktuellem Status.

        Flow (siehe docs/modules/wb_license_client.md):
          1. Key aus ir.config_parameter lesen
          2. Cache-Eintrag (wb.license.info) prüfen
          3. Wenn Cache < 24h alt: Cache zurückgeben (kein HTTP-Call)
          4. Sonst: Ping an Server, Cache aktualisieren
          5. Bei Server-Fehler: alten Cache behalten, state bleibt wie es war;
             der nächste Ping frühestens nach PING_RETRY_MINUTES
          6. is_valid berücksichtigt min_cache_age_days für 'unknown'-State

        :param product_code: 4-stelliger Produkt-Code (z.B. 'TELE')
        :param min_cache_age_days: Max. Alter der letzten erfolgreichen
            Prüfung für 'unknown'-State. Default 30. Strenger bei Methoden
            mit externen Kosten.
        :param module_name: Optional technischer Modul-Name (z.B.
            'wb_bitwarden_pro'). Wenn übergeben und der Cache hat noch
            keinen, wird das Mapping persistiert — danach kann der Ping
            die installierte Modul-Version melden.
        :return: wb.license.info Record (erstellt einen, wenn noch keiner existiert)
        """
        info = self._get_or_create_info(product_code, module_name=module_name)
        key = self._get_stored_key(product_code)

        if not key:
            info.write({'state': 'unlicensed', 'key': False})
            self._maybe_announce_install(info, product_code)
            return info
        if not self._key_matches(product_code, key):
            # Schlüssel eines anderen Produkts: gilt nicht, kein Ping (#97, I4)
            info._mark_product_mismatch()
            return info

        now = fields.Datetime.now()
        needs_refresh = (
            not info.last_server_check
            or info.last_server_check < now - timedelta(hours=24)
        )
        # Server war gerade nicht erreichbar: nicht bei jedem Aufruf erneut bis
        # zum Timeout warten, sondern höchstens alle PING_RETRY_MINUTES.
        recently_failed = (
            info.last_failed_check_at
            and now - timedelta(minutes=PING_RETRY_MINUTES) < info.last_failed_check_at <= now
        )
        sperre = _PING_SPERRE.get((self.env.cr.dbname, product_code))
        if sperre is not None and time.monotonic() - sperre < PING_RETRY_MINUTES * 60:
            recently_failed = True
        if needs_refresh and not recently_failed:
            self._do_ping(info, product_code, key)

        info.apply_cache_age_policy(min_cache_age_days)
        return info

    @api.private
    @api.model
    def activate_license(self, product_code, key, activation_code, consents=None):
        """Ruft /api/license/activate auf dem Server auf.

        Bei Erfolg: Key wird in ir.config_parameter gespeichert, Cache
        aktualisiert. Bei Fehler: UserError mit aussagekräftiger Meldung.

        Wird vom Activate-Wizard aufgerufen.

        :param consents: Optional dict mit den Activate-Consents:
            ``{'confirm_eula': bool, 'confirm_terms': bool,
              'confirm_privacy': bool, 'confirm_no_refund': bool,
              'subscribe_newsletter': bool, 'contact_email': str}``.
            Werden 1:1 als Payload-Keys an den Server durchgereicht.
            Server validiert die Pflicht-Consents und gibt
            ``MISSING_CONSENTS`` zurück, wenn welche fehlen.
        """
        self._ensure_admin()
        product_code = (product_code or '').strip().upper()
        if not self._key_matches(product_code, key):
            raise UserError(_(
                "This license key does not belong to product %s. Please check the "
                "product code and the key.") % product_code)
        payload = {
            'key': key,
            'product_code': product_code,
            'activation_code': activation_code,
            'domain': self._get_domain(),
            'db_uuid': self._get_db_uuid(),
            'email': self.env.user.email or '',
            'client_version': self._get_module_version(),
            # Sprint 3 / L-C1 — Replay-Schutz: jeder Activate-Call kriegt
            # eine UUID4. Server speichert das Resultat in
            # wb.license.activation_request — bei identischer ID innerhalb
            # 7 Tagen wird der Original-Payload zurueckgegeben statt einer
            # zweiten echten Activation. Schuetzt vor DB-Clone-Replay und
            # versehentlichen Doppel-Submits.
            'request_id': str(uuid.uuid4()),
        }
        if consents:
            payload.update({
                'confirm_eula': bool(consents.get('confirm_eula')),
                'confirm_terms': bool(consents.get('confirm_terms')),
                'confirm_privacy': bool(consents.get('confirm_privacy')),
                'confirm_no_refund': bool(consents.get('confirm_no_refund')),
                'subscribe_newsletter': bool(consents.get('subscribe_newsletter')),
                'contact_email': (consents.get('contact_email') or '').strip(),
            })
        data, status = self._do_request(
            '/api/license/activate', payload, timeout=DEFAULT_ACTIVATE_TIMEOUT,
        )

        if status == 200 and data and data.get('status') == 'ok':
            self._store_key(product_code, key)
            info = self._get_or_create_info(product_code)
            vals = {
                'key': key,
                'state': 'active',
                'valid_to': data.get('valid_to'),
                'last_server_check': fields.Datetime.now(),
                'last_check_success': True,
                'last_error_message': False,
            }
            max_users = parse_max_users(data)
            if max_users is not None:
                vals['max_users'] = max_users
            info.write(vals)
            return info

        self._raise_activation_error(status, data)

    @api.private
    @api.model
    def start_trial(self, product_code, contact_name, contact_email, company_name):
        """Request a free trial from /api/license/trial and store its key (DECISIONS.md #94).

        Called by the wizard only after the user agreed to send the data. Sends
        name, email and company of the form plus domain and database UUID of
        this instance (the trial key is bound to them). Stores nothing unless
        the server answers with a valid trial key.

        :return: ``('ok', info)`` or ``('pending', None)`` (request is reviewed
            manually by WISSEN BERATUNG; repeating it later fetches the key).
        :raises UserError: on any error; then no key is stored.
        """
        # Nur Administratoren dürfen Daten an WB senden und einen Schlüssel eintragen.
        self._ensure_admin()
        product_code = (product_code or '').strip().upper()
        if self._get_stored_key(product_code):
            raise UserError(_(
                "A license key for %s is already configured. A trial is only "
                "available for new installations.") % product_code)
        domain = self._get_domain() or ''
        db_uuid = self._get_db_uuid() or ''
        if not domain or not db_uuid:
            raise UserError(_(
                "The domain or database ID of this Odoo instance could not be "
                "determined. Please inform your system administrator."))
        payload = {
            'product_code': product_code,
            'contact_name': (contact_name or '').strip(),
            'contact_email': (contact_email or '').strip(),
            'company_name': (company_name or '').strip(),
            'domain': domain,
            'db_uuid': db_uuid,
            'client_version': self._get_module_version(),
            # Nur nach angehaktem Kästchen im Formular aufgerufen (#97, I6)
            'consent': True,
            'privacy_version': PRIVACY_VERSION,
        }
        data, status = self._do_request(
            '/api/license/trial', payload, timeout=DEFAULT_TRIAL_TIMEOUT)
        data = data if isinstance(data, dict) else {}

        if status == 200 and data.get('status') == 'pending':
            return 'pending', None
        key = (data.get('key') or '').strip() if status == 200 and data.get('status') == 'ok' else ''
        max_users = parse_max_users(data)
        valid_to = data.get('valid_to') if isinstance(data.get('valid_to'), str) else ''
        if key and (max_users is None or not valid_to):
            # Fail-closed: unbrauchbare Antwort, nichts speichern (#97, M3)
            raise UserError(_(
                "The license server sent an invalid answer. No trial was started. "
                "Please contact support@wissen-beratung.de."))
        if self._key_matches(product_code, key):
            self._store_key(product_code, key)
            info = self._get_or_create_info(product_code)
            state = data.get('state') if data.get('state') in ('trial', 'active') else 'trial'
            info.write({
                'key': key,
                'state': state,
                'valid_from': data.get('valid_from') or False,
                'valid_to': valid_to,
                'max_users': max_users,
                'last_server_check': fields.Datetime.now(),
                'last_check_success': True,
                'last_error_message': False,
                'last_failed_check_at': False,
                'instance_status': False,
                'instance_until': False,
            })
            return 'ok', info
        self._raise_trial_error(status, data)

    @api.model
    def _raise_trial_error(self, status, data):
        """Translate an error answer of /api/license/trial into a UserError."""
        error_code = (data or {}).get('error')
        # Ein Text für alle „nicht möglich“-Fälle; die Einzelcodes kommen nur
        # noch von älteren Servern (#97, I2).
        not_available = _(
            "A free trial is not available for this installation. To test or buy "
            "the product, please contact vertrieb@wissen-beratung.de.")
        messages = {
            'TRIAL_NOT_AVAILABLE': not_available,
            'EMAIL_LIMIT_REACHED': not_available,
            'DOMAIN_LIMIT_REACHED': not_available,
            'DATABASE_LIMIT_REACHED': not_available,
            'TRIAL_ALREADY_USED': not_available,
            'MISSING_CONSENT': _(
                "Please agree to the transfer of your data to WISSEN BERATUNG; "
                "without it no trial can be started."),
            'PRODUCT_NOT_FOUND': _(
                "The license server does not know this product. Please contact "
                "support@wissen-beratung.de."),
            'INVALID_EMAIL': _("Please enter a valid email address."),
            'MISSING_COMPANY': _("Please enter your company."),
            'MISSING_DOMAIN': _(
                "The address of this Odoo instance could not be determined. Please "
                "inform your system administrator."),
            'INVALID_DB_UUID': _(
                "The database ID of this Odoo instance is invalid. Please inform "
                "your system administrator."),
            'INVALID_PRODUCT_CODE': _(
                "Invalid product code. Please contact support@wissen-beratung.de."),
            'TOO_MANY_REQUESTS': _("Too many requests. Please try again in an hour."),
        }
        message = messages.get(error_code)
        if not message:
            if status == 0:
                message = _(
                    "The license server cannot be reached. Please check the internet "
                    "connection and try again. No trial was started.")
            else:
                message = _(
                    "The trial could not be started (HTTP %s). Please try again later "
                    "or contact support@wissen-beratung.de.") % status
        raise UserError(message)

    @api.model
    def _wb_license_max_users(self, product_code):
        """Public helper for product modules: user limit of the current license.

        :return: ``None`` if there is no valid license for ``product_code``,
            ``0`` for unlimited, otherwise the maximum number of users reported
            by the license server.
        """
        info = self.check_license(product_code)
        if not info.is_valid:
            return None
        return info.max_users or 0

    @api.private
    @api.model
    def request_migration(self, product_code, new_domain, new_db_uuid, reason, contact_email):
        """Ruft /api/license/migrate auf dem Server auf."""
        self._ensure_admin()
        key = self._get_stored_key(product_code)
        if not key:
            raise UserError(_(
                "No license configured for product '%s', migration not possible."
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
                "The migration could not be requested (HTTP %s). Please try again later "
                "or contact support@wissen-beratung.de."
            ) % status)
        return data

    @api.private
    @api.model
    def submit_lead_request(self, product_code, payload):
        """Sendet einen Lead-/Verkaufschancen-Request an den Lizenzserver (my.wissen-beratung.de).

        Wird aus Onboarding- bzw. Lizenz-Anfrage-Wizards der Produkt-Module
        aufgerufen. Der Server-Endpoint ``/api/license/lead`` legt einen
        ``crm.lead`` (intent='info') bzw. eine Verkaufschance (intent='purchase')
        an und reichert den ``wb.license.install``-Eintrag an.

        Identifikation der Quelle erfolgt über ``db_uuid`` + ``domain``
        (werden hier automatisch ergänzt). Auth ist 'public' analog zu den
        anderen Endpoints; Spam-Schutz via Server-Rate-Limit.

        :param product_code: 4-Char Produkt-Code (z.B. 'BITW')
        :param payload: dict mit Wizard-Feldern. Erlaubte Keys:
            ``intent`` ('info'|'purchase'), ``contact_email``, ``contact_name``,
            ``contact_phone``, ``company_name``, ``company_vat``,
            ``company_street``, ``company_zip``, ``company_city``,
            ``company_country_code``, ``notes``.
        :return: dict mit Server-Response (``status``, ``install_id``,
            optional ``lead_id``).
        :raises UserError: bei Server-Fehler oder Validierungsfehler.
        """
        body = dict(payload or {})
        body['product_code'] = (product_code or '').strip().upper()
        body['domain'] = self._get_domain() or ''
        body['db_uuid'] = self._get_db_uuid() or ''
        body['client_version'] = self._get_module_version()

        if not body['domain'] or not body['db_uuid']:
            raise UserError(_(
                "The domain or database ID of this Odoo instance could not be "
                "determined. Please inform your system administrator."))

        # Signatur ist hier optional: ein Interessent hat noch keinen Key.
        # Ein Bestandskunde, der ein zweites Produkt anfragt, signiert mit
        # dem Secret des vorhandenen Schluessels und hebt den Eingang damit
        # serverseitig von 'unverified' auf 'hmac'.
        lead_key = self._get_stored_key(body['product_code'])
        data, status = self._do_request(
            '/api/license/lead', body, timeout=DEFAULT_ACTIVATE_TIMEOUT,
            key=lead_key or None,
            secret=self._get_stored_secret(body['product_code']) if lead_key else None,
        )

        if status == 200 and data and data.get('status') == 'ok':
            return data

        error_code = (data or {}).get('error') if data else None
        messages = {
            'TOO_MANY_REQUESTS': _("Too many requests. Please try again in an hour."),
            'INVALID_PRODUCT_CODE': _(
                "Invalid product code. Please contact support@wissen-beratung.de."),
            'MISSING_BINDING_DATA': _(
                "The domain or database ID of this Odoo instance could not be "
                "determined. Please inform your system administrator."),
            'MISSING_REQUIRED_FIELDS': _(
                "Please fill in company, contact person and email."),
        }
        message = messages.get(error_code) or (
            _("The license server cannot be reached (HTTP %s). Please try again "
              "later or contact vertrieb@wissen-beratung.de.") % status
            if status == 0 or status >= 500
            else _("The request could not be sent (HTTP %s). Please check your "
                   "input and try again.") % status
        )
        raise UserError(message)

    @api.private
    @api.model
    def register_install(self, product_code, module_name=None, try_auto_lookup=True):
        """Server-side entry point for install hooks of product modules (not callable over RPC)."""
        return self._register_install(product_code, module_name=module_name,
                                      try_auto_lookup=try_auto_lookup)

    @api.model
    def _register_install(self, product_code, module_name=None,
                          try_auto_lookup=True):
        """Meldet diese Odoo-Instanz als Install beim Lizenzserver (my.wissen-beratung.de) an.

        Wird aus zwei Pfaden gerufen:
        1. ``_post_init_hook`` von Produkt-Modulen — explizit beim Install,
           hier sollte ``module_name`` mitgegeben werden.
        2. ``check_license`` — Fallback wenn (noch) kein Key gespeichert ist
           und der letzte Announce > 24h zurückliegt.

        Best-effort: Fehler werden geloggt, aber nie geworfen — ein
        unerreichbarer Lizenz-Server darf den Module-Install nicht crashen.
        Opt-out via ``ir.config_parameter`` ``wb_license_client.disable_install_registry=True``.

        :param product_code: 4-Char Produkt-Code (z.B. 'TELE')
        :param module_name: Optional technischer Modul-Name (z.B.
            'wb_bitwarden_pro'). Wird auf wb.license.info persistiert,
            damit der tägliche Ping die installierte Modul-Version melden kann.
        :param try_auto_lookup: Wenn True (default) und kein Key gespeichert,
            wird /api/license/lookup probiert — Zero-Touch-Aktivierung wenn
            WB die Lizenz beim Verkauf armiert hat.
        :return: True wenn Announce erfolgreich, False sonst
        """
        if self._install_registry_disabled():
            return False

        info = self._get_or_create_info(product_code, module_name=module_name)
        announce_ok = self._announce_install(info, product_code)

        # Zero-Touch: wenn noch kein Key konfiguriert ist, einmal versuchen
        # ob WB eine armierte Lizenz fuer (domain, db_uuid, email) hat.
        # Best-effort — bei Fehler stiller Fallback auf manuellen Wizard.
        if try_auto_lookup and not self._get_stored_key(product_code):
            try:
                self._try_auto_lookup(info, product_code)
            except Exception as e:
                _logger.warning(
                    "[wb_license_client] Auto-Lookup fuer %s gescheitert: %s",
                    product_code, e)

        return announce_ok

    @api.model
    def _try_auto_lookup(self, info, product_code):
        """Ruft /api/license/lookup und speichert Key bei Match.

        Liefert True wenn Match gefunden + Key gespeichert, sonst False.
        Wird aus ``register_install`` und aus ``_post_init_auto_lookup``
        gerufen.
        """
        domain = self._get_domain()
        db_uuid = self._get_db_uuid()
        if not db_uuid:
            return False
        email = self.env.user.email or ''
        if not domain and not email:
            return False
        payload = {
            'product_code': product_code,
            'db_uuid': db_uuid,
            'domain': domain or '',
            'email': email,
        }
        data, status = self._do_request(
            '/api/license/lookup', payload, timeout=DEFAULT_ACTIVATE_TIMEOUT,
        )
        if status != 200 or not isinstance(data, dict):
            return False
        if not data.get('found'):
            _logger.info(
                "[wb_license_client] Auto-Lookup %s: kein Match.", product_code)
            return False
        key = (data.get('key') or '').strip()
        if not self._key_matches(product_code, key):
            _logger.warning(
                "[wb_license_client] Auto-Lookup %s: Schlüssel gehört nicht zum Produkt, "
                "ignoriert.", product_code)
            return False
        self._store_key(product_code, key)
        vals = {
            'key': key,
            'state': data.get('state') or 'active',
            'valid_to': data.get('valid_to'),
            'last_server_check': fields.Datetime.now(),
            'last_check_success': True,
            'last_error_message': False,
        }
        max_users = parse_max_users(data)
        if max_users is not None:
            vals['max_users'] = max_users
        info.sudo().write(vals)
        _logger.info(
            "[wb_license_client] Auto-Lookup %s: Match — Lizenz '%s' "
            "automatisch gebunden.", product_code, key)
        return True

    @api.private
    @api.model
    def auto_lookup_all_installed(self):
        """Iteriert ueber alle Module, die ``wb_license_client`` als
        Dependency haben und einen 4-Buchstaben-Produkt-Code via
        ``ir.config_parameter`` ``wb_license_client.product_code_<modul>``
        oder via Modul-Eigenschaft melden, und versucht /api/license/lookup
        fuer jeden, der noch keinen Key gespeichert hat.

        Wird vom post_init_hook von wb_license_client und manuell aus dem
        Settings-Dialog aufrufbar — z.B. nach Server-Migration kann der
        Admin damit alle Lizenzen neu auflosen lassen.
        """
        self._ensure_admin()
        Module = self.env['ir.module.module'].sudo()
        IrConfig = self.env['ir.config_parameter'].sudo()
        installed = Module.search([('state', '=', 'installed')])
        wlc = installed.filtered(
            lambda m: 'wb_license_client' in (m.dependencies_id.mapped('name') or [])
        )
        results = {}
        for module in wlc:
            product_code = (IrConfig.get_param(
                'wb_license_client.product_code_' + module.name) or '').strip().upper()
            if not product_code or len(product_code) != 4:
                continue
            if self._get_stored_key(product_code):
                continue
            try:
                info = self._get_or_create_info(product_code, module_name=module.name)
                results[product_code] = self._try_auto_lookup(info, product_code)
            except Exception as e:
                _logger.warning(
                    "[wb_license_client] auto_lookup_all_installed %s: %s",
                    product_code, e)
                results[product_code] = False
        return results

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
        module_version = self._get_module_version_for(info.module_technical_name)
        if module_version:
            payload['module_version'] = module_version
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
    def _get_or_create_info(self, product_code, module_name=None):
        """Findet oder erzeugt den wb.license.info-Record für (product, company).

        Wenn ``module_name`` übergeben wird und der Record noch keinen hat
        (oder er sich geändert hat — z.B. Pro/Free-Switch), wird das Feld
        ``module_technical_name`` gesetzt. Damit kann der nächste Ping die
        installierte Modul-Version melden.
        """
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
                'module_technical_name': module_name or False,
            })
        elif module_name and info.module_technical_name != module_name:
            info.write({'module_technical_name': module_name})
        return info

    @api.model
    def _do_ping(self, info, product_code, key):
        """Internal HTTP-Call /api/license/check."""
        if not self._key_matches(product_code, key):
            info._mark_product_mismatch()
            return
        payload = {
            'key': key,
            'product_code': product_code,
            'domain': self._get_domain(),
            'db_uuid': self._get_db_uuid(),
            'client_version': self._get_module_version(),
            'is_neutralized': self._is_neutralized(),
        }
        module_version = self._get_module_version_for(info.module_technical_name)
        if module_version:
            payload['module_version'] = module_version
        data, status = self._do_request(
            '/api/license/check', payload,
            key=key, secret=self._get_stored_secret(product_code),
        )
        sperre_key = (self.env.cr.dbname, product_code)
        if status == 200 and isinstance(data, dict) and (
                data.get('error') == 'PRODUCT_MISMATCH'
                or (data.get('product_code') and data.get('product_code') != product_code)):
            # Antwort für ein anderes Produkt: gilt als abgelaufen (#97, I4)
            _PING_SPERRE.pop(sperre_key, None)
            info._mark_product_mismatch()
        elif status == 200 and data:
            _PING_SPERRE.pop(sperre_key, None)
            info.apply_server_response(data)
        else:
            _PING_SPERRE[sperre_key] = time.monotonic()
            info.record_check_failure(status, data)

    @api.model
    def _do_request(self, endpoint, payload, timeout=DEFAULT_CHECK_TIMEOUT,
                    key=None, secret=None):
        """HTTP-POST mit Timeout und Error-Handling.

        :param key: Public Key fuer die HMAC-Signatur. Ohne key/secret geht
            der Request unsigniert raus — der Server toleriert das, solange
            ``wb_subscription.hmac_enforcement`` nicht auf ``required`` steht
            und fuer diesen Key ein Secret hinterlegt ist.
        :param secret: API-Secret aus ir.config_parameter.

        Returns:
            (response_json_or_None, status_code_or_0)
            status=0 signalisiert Netzwerk-Fehler (Timeout/ConnectionError).
        """
        url = self._get_server_url() + endpoint
        # X-Odoo-Database (M-109): Der Header waehlt auf einem Multi-DB-
        # Lizenzserver die Datenbank des SERVERS. Den Namen der Kunden-DB
        # senden wir nie: Existiert sie dort nicht, antwortet der Server mit
        # 404 ("No database is selected"). Gesetzt wird der Header nur, wenn
        # der Parameter ``wb_license_client.server_database`` den DB-Namen
        # des Lizenzservers nennt (z.B. WB selbst bei Self-Hosting mit
        # Client und Server in einer Multi-DB-Instanz).
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': f'wb_license_client/{self._get_module_version()}',
            'X-WB-DB-UUID': self._get_db_uuid() or '',
            'X-WB-Domain': self._get_domain() or '',
        }
        server_db = (self.env['ir.config_parameter'].sudo().get_param(
            SERVER_DATABASE_PARAM) or '').strip()
        if server_db:
            headers['X-Odoo-Database'] = server_db
        # HMAC-Signatur, wenn fuer dieses Produkt ein Secret hinterlegt ist.
        # Signiert wird der rohe Body — deshalb wird hier selbst serialisiert
        # und ``data=`` statt ``json=`` verwendet: requests wuerde sonst neu
        # serialisieren und die Signatur passte nicht mehr zum Body.
        envelope = {'jsonrpc': '2.0', 'method': 'call', 'params': payload}
        body = json.dumps(envelope, separators=(',', ':'), ensure_ascii=False)
        body_bytes = body.encode('utf-8')
        if secret and key:
            headers.update(self._signature_headers(secret, key, body_bytes))

        try:
            response = requests.post(url, data=body_bytes, headers=headers, timeout=timeout)
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
                "The license key has an invalid format. Please enter it exactly as "
                "shown on the certificate."),
            'INVALID_CODE_FORMAT': _(
                "The activation code has an invalid format: 5 groups of 5 characters, "
                "separated by hyphens."),
            'KEY_NOT_FOUND': _(
                "This license key is unknown. Please check your input or contact "
                "support@wissen-beratung.de."),
            'WRONG_CODE': _(
                "The activation code is wrong. Please check the code in the portal "
                "and try again."),
            'ALREADY_ACTIVATED': _(
                "This license key has already been activated on another instance. "
                "Do you want to request a migration?"),
            'ACTIVATION_EXPIRED': _(
                "The activation period has expired. Please contact "
                "support@wissen-beratung.de to reactivate it."),
            'TOO_MANY_ATTEMPTS': _(
                "Too many failed attempts. Please wait one hour and try again."),
            'MISSING_CONSENTS': _(
                "Activation not possible: you have to confirm the EULA, the terms and "
                "conditions, the privacy notice and the waiver of a refund."),
        }
        message = messages.get(error_code)
        if not message:
            if status == 0:
                message = _(
                    "The license server cannot be reached. Please check your internet "
                    "connection and try again.")
            else:
                message = _(
                    "Activation failed (HTTP %s). Please contact "
                    "support@wissen-beratung.de.") % status
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
    def _signature_headers(self, secret, key, body_bytes):
        """Baut die Signatur-Header (Schema v1).

        Gegenstueck zu ``wb.key.generator.build_signature_base`` im
        wb_subscription-Modul und zu ``Signature.php`` im PHP-Client.
        Signiert wird der SHA256 des rohen Bodys, zusammen mit Key,
        Timestamp und einer Einmal-Nonce — der Server lehnt eine zweite
        Verwendung derselben Nonce ab.
        """
        timestamp = str(int(time.time()))
        nonce = secrets.token_hex(16)
        base = '\n'.join([
            SIGNATURE_SCHEME, key, timestamp, nonce,
            hashlib.sha256(body_bytes).hexdigest(),
        ])
        signature = hmac.new(
            secret.encode('utf-8'), base.encode('utf-8'), hashlib.sha256,
        ).hexdigest()
        return {
            'X-WB-Key': key,
            'X-WB-Timestamp': timestamp,
            'X-WB-Nonce': nonce,
            'X-WB-Signature': 'v1=' + signature,
        }

    @api.model
    def _get_stored_secret(self, product_code):
        """API-Secret fuer HMAC-signierte Requests.

        Wird von WB zusammen mit dem Lizenzschluessel ausgegeben und hier
        eingetragen. Fehlt es, laeuft alles unsigniert weiter.
        """
        return self.env['ir.config_parameter'].sudo().get_param(
            SECRET_PARAM_PREFIX + product_code) or False

    @api.model
    def _store_secret(self, product_code, secret):
        self.env['ir.config_parameter'].sudo().set_param(
            SECRET_PARAM_PREFIX + product_code, secret or '')

    @api.model
    def _get_server_url(self):
        return self.env['ir.config_parameter'].sudo().get_param(
            SERVER_URL_PARAM, DEFAULT_SERVER_URL).rstrip('/')

    @api.model
    def _get_db_uuid(self):
        return self.env['ir.config_parameter'].sudo().get_param('database.uuid')

    @api.model
    def _is_neutralized(self):
        """True für neutralisierte Datenbanken (Kopie, Staging, Testsystem).

        Gleiche Auswertung wie Odoo selbst in ir_cron: der Wahrheitswert von
        ``database.is_neutralized``. Der Server vergibt daraufhin Eval oder
        DEV-Lizenz statt einer zweiten Produktivinstanz.
        """
        return bool(self.env['ir.config_parameter'].sudo().get_param(
            'database.is_neutralized'))

    @api.model
    def _get_domain(self):
        return self.env['ir.config_parameter'].sudo().get_param('web.base.url')

    @api.model
    def _get_module_version(self):
        module = self.env['ir.module.module'].sudo().search(
            [('name', '=', 'wb_license_client')], limit=1)
        return module.latest_version or '19.0.1.0.0'

    @api.model
    def _get_module_version_for(self, module_name):
        """Liest die installierte Version eines beliebigen Odoo-Moduls.

        Returns False wenn ``module_name`` leer ist oder das Modul nicht
        gefunden wird — der Caller entscheidet dann, ob trotzdem gepingt
        wird (ohne module_version-Feld) oder nicht.
        """
        if not module_name:
            return False
        module = self.env['ir.module.module'].sudo().search(
            [('name', '=', module_name)], limit=1)
        return module.latest_version or False

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
                    "No valid license for '%s'. Please activate one under "
                    "Settings → WB Licenses."
                ) % product_code)
            return func(self, *args, **kwargs)
        return wrapper
    return decorator
