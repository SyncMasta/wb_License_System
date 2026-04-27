from odoo import api, fields, models


SERVER_URL_PARAM = 'wb_license_client.server_url'
DEBUG_PARAM = 'wb_license_client.debug_mode'


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    wb_license_server_url = fields.Char(
        string='WB-Lizenz-Server-URL',
        config_parameter=SERVER_URL_PARAM,
        default='https://wissen-beratung.de',
        help="URL des WB-Lizenz-Servers. Normalerweise nicht ändern. "
             "Nur für Test-/Staging-Umgebungen anpassen.",
    )
    wb_license_debug_mode = fields.Boolean(
        string='Debug-Modus',
        config_parameter=DEBUG_PARAM,
        help="Wenn aktiv, werden Server-Responses als Notification angezeigt. "
             "Nicht in Produktion aktivieren!",
    )
