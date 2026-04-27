"""Interne Controller — nur für authenticated Backend-User.

Wird vom Banner-JS aufgerufen, um alle Lizenz-Infos zu laden und
Problem-States visuell anzuzeigen.
"""

from odoo import http
from odoo.http import request


class WbLicenseClientController(http.Controller):

    @http.route('/wb_license_client/get_all_infos',
                type='json', auth='user', methods=['POST'])
    def get_all_license_infos(self):
        """Gibt alle wb.license.info-Records der aktuellen Company zurück.

        Nur für Banner-Anzeige — keine geheimen Daten (Keys sind maskiert).
        """
        infos = request.env['wb.license.info'].search([
            ('company_id', '=', request.env.company.id),
        ])
        return [{
            'id': info.id,
            'product_code': info.product_code,
            'state': info.state,
            'key_masked': info.key_masked,
            'valid_to': info.valid_to.isoformat() if info.valid_to else None,
            'grace_until': info.grace_until.isoformat() if info.grace_until else None,
            'days_remaining': info.days_remaining,
            'is_valid': info.is_valid,
            'user_message': info.user_message,
        } for info in infos]
