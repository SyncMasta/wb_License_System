"""Standard-URL des Lizenzservers von der Website auf my.wissen-beratung.de stellen.

Die Hosts wissen-beratung.de und www.wissen-beratung.de liefern die Marketing-
Website (kein Odoo), jeder Lizenzaufruf endete dort mit 404. Eigene Werte
bleiben unangetastet.
"""
from odoo import SUPERUSER_ID, api

ALTE_STANDARDS = ('https://wissen-beratung.de', 'https://www.wissen-beratung.de')
NEU = 'https://my.wissen-beratung.de'
PARAM = 'wb_license_client.server_url'


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    params = env['ir.config_parameter'].sudo()
    if params.get_param(PARAM) in ALTE_STANDARDS:
        params.set_param(PARAM, NEU)
