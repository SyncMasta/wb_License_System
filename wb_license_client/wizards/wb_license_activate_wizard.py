"""Activate-Wizard.

Kunde gibt Key + Activation-Code ein, Wizard ruft den Server auf und
speichert bei Erfolg den Key in ir.config_parameter.

Format-Validation läuft vor dem HTTP-Call (spart Rate-Limit-Trefferquote).
"""

import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


KEY_FORMAT_RE = re.compile(r'^WB-([A-Z]{4})-([a-f0-9]{8})([A-Z2-7]{2})$')
CODE_FORMAT_RE = re.compile(r'^[A-HJ-NP-Z2-9]{5}(-[A-HJ-NP-Z2-9]{5}){4}$')


class WbLicenseActivateWizard(models.TransientModel):
    _name = 'wb.license.activate.wizard'
    _description = 'Wizard zur Lizenz-Aktivierung'

    product_code = fields.Char(
        size=4,
        required=True,
        help="4-stelliger Produkt-Code (z.B. 'TELE'). Steht auf dem Lizenz-Zertifikat.",
    )
    key = fields.Char(
        string='Lizenzschlüssel',
        required=True,
        help="Format: WB-XXXX-xxxxxxxxXX",
    )
    activation_code = fields.Char(
        string='Aktivierungs-Code',
        required=True,
        help="25 Zeichen in 5er-Gruppen, aus dem Portal-Download.",
    )
    info_message = fields.Html(compute='_compute_info_message')

    @api.depends('product_code')
    def _compute_info_message(self):
        for rec in self:
            rec.info_message = _(
                "<p><strong>So aktivieren Sie Ihre Lizenz:</strong></p>"
                "<ol>"
                "<li>Geben Sie den Lizenzschlüssel ein (vom Zertifikat)</li>"
                "<li>Klicken Sie auf den Ticket-Link aus der Aktivierungs-Email</li>"
                "<li>Geben Sie den 6-stelligen Bestätigungs-Code ein (Email)</li>"
                "<li>Kopieren Sie den 25-stelligen Aktivierungs-Code vom Portal "
                "und fügen Sie ihn unten ein</li>"
                "</ol>"
            )

    @api.constrains('key')
    def _check_key_format(self):
        for rec in self:
            if rec.key and not KEY_FORMAT_RE.match(rec.key.strip()):
                raise ValidationError(_(
                    "Lizenzschlüssel hat ein ungültiges Format. "
                    "Erwartet: WB-XXXX-xxxxxxxxXX (Beispiel: WB-TELE-a3f28c919K)"
                ))

    @api.constrains('activation_code')
    def _check_code_format(self):
        for rec in self:
            if rec.activation_code and not CODE_FORMAT_RE.match(rec.activation_code.strip()):
                raise ValidationError(_(
                    "Aktivierungs-Code hat ein ungültiges Format. "
                    "Erwartet: 5 Gruppen à 5 Zeichen, mit Bindestrichen "
                    "(Beispiel: 7H3K9-M4P2N-RQ8T2-W5X7Y-A9B3F)"
                ))

    @api.constrains('product_code')
    def _check_product_code(self):
        for rec in self:
            if rec.product_code and not re.match(r'^[A-Z]{4}$', rec.product_code):
                raise ValidationError(_(
                    "Produkt-Code muss genau 4 Großbuchstaben sein."
                ))

    def action_activate(self):
        self.ensure_one()
        key = (self.key or '').strip()
        code = (self.activation_code or '').strip()
        product_code = (self.product_code or '').strip().upper()

        info = self.env['wb.license.client'].activate_license(
            product_code, key, code,
        )

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'title': _("Lizenz aktiviert"),
                'message': _(
                    "Die Lizenz für %s ist jetzt aktiv. Gültig bis %s."
                ) % (product_code, info.valid_to or '-'),
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
