"""Standalone Telegram-Alert-Notifier.

Bewusst NICHT abhängig von wb_odoo_automations — wb_subscription muss
laut Tobias' Prinzip ('jedes Modul standalone installierbar') eigenständig
funktionieren.

Wenn Token + Chat-ID nicht konfiguriert sind: silent no-op (warning ins Log).
Wenn das Modul wb_odoo_automations parallel installiert ist: kann Tobias
in seinen System-Parametern dieselben Werte hinterlegen oder einen
ir.actions.server schreiben, der wb_subscription's Calls weiterreicht.
"""

import json
import logging

import requests

from odoo import api, models

_logger = logging.getLogger(__name__)


TELEGRAM_TOKEN_PARAM = 'wb_subscription.telegram_token'
TELEGRAM_CHAT_ID_PARAM = 'wb_subscription.telegram_chat_id'
TELEGRAM_API_URL = 'https://api.telegram.org/bot{token}/sendMessage'
DEFAULT_TIMEOUT = 8


class WbTelegramNotifier(models.AbstractModel):
    _name = 'wb.telegram.notifier'
    _description = 'Telegram-Alert-Service für Lizenz-Events'

    @api.model
    def is_configured(self):
        icp = self.env['ir.config_parameter'].sudo()
        return bool(icp.get_param(TELEGRAM_TOKEN_PARAM) and icp.get_param(TELEGRAM_CHAT_ID_PARAM))

    @api.model
    def send_message(self, message, chat_ids=None, parse_mode='HTML',
                     disable_web_preview=True, silent=False):
        """Sendet eine Nachricht an den konfigurierten Tobias-Chat.

        Returns:
            True wenn versendet, False wenn nicht konfiguriert oder Fehler.

        WICHTIG: Niemals Activation-Codes oder Klartext-Secrets in `message`
        einbetten. Die Nachricht landet in Telegram-Servern und im
        wb.notification.log.

        Signatur-Hinweis: Diese standalone-Variante teilt sich den _name
        'wb.telegram.notifier' bewusst mit dem gleichnamigen Modell aus
        wb_odoo_automations (beide Module standalone-installierbar). Bei
        paralleler Installation gewinnt eine der beiden Implementierungen in
        der MRO. Damit Aufrufer beider Module unabhaengig von der Ladeordnung
        funktionieren, akzeptiert und bedient diese Methode dieselbe
        Superset-Signatur (chat_ids/disable_web_preview/silent). `chat_ids`
        wird hier ignoriert (fester Chat aus ir.config_parameter) und existiert
        nur zur Signatur-Kompatibilitaet.
        """
        icp = self.env['ir.config_parameter'].sudo()
        token = icp.get_param(TELEGRAM_TOKEN_PARAM)
        chat_id = icp.get_param(TELEGRAM_CHAT_ID_PARAM)

        if not token or not chat_id:
            _logger.warning(
                "[wb_subscription] Telegram nicht konfiguriert "
                "(ir.config_parameter %s + %s) — überspringe Send",
                TELEGRAM_TOKEN_PARAM, TELEGRAM_CHAT_ID_PARAM,
            )
            return False

        url = TELEGRAM_API_URL.format(token=token)
        try:
            response = requests.post(
                url,
                data={
                    'chat_id': chat_id,
                    'text': message,
                    'parse_mode': parse_mode,
                    'disable_web_page_preview': disable_web_preview,
                    'disable_notification': silent,
                },
                timeout=DEFAULT_TIMEOUT,
            )
            if response.status_code == 200:
                return True
            _logger.warning(
                "[wb_subscription] Telegram-API antwortete mit %s: %s",
                response.status_code, response.text[:200],
            )
            return False
        except requests.RequestException as e:
            _logger.exception("[wb_subscription] Telegram-Send fehlgeschlagen: %s", e)
            return False
