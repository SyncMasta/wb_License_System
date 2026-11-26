from odoo import api, fields, models


SERVER_URL_PARAM = 'wb_license_client.server_url'
DEBUG_PARAM = 'wb_license_client.debug_mode'
INSTALL_REGISTRY_OPT_OUT_PARAM = 'wb_license_client.disable_install_registry'


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    wb_license_server_url = fields.Char(
        string='WB License Server URL',
        config_parameter=SERVER_URL_PARAM,
        default='https://my.wissen-beratung.de',
        help="URL of the WB license server. Normally do not change it; only "
             "for test or staging environments.",
    )
    wb_license_debug_mode = fields.Boolean(
        string='Debug Mode',
        config_parameter=DEBUG_PARAM,
        help="If set, server answers are shown as notifications. "
             "Do not enable in production!",
    )
    wb_license_disable_install_registry = fields.Boolean(
        string='Disable Install Registry',
        config_parameter=INSTALL_REGISTRY_OPT_OUT_PARAM,
        help="If set, installing an unlicensed WB product add-on sends NO "
             "announcement to my.wissen-beratung.de. Off by default; the "
             "announcement only contains domain, database ID, product code and "
             "admin email for lead management.",
    )
