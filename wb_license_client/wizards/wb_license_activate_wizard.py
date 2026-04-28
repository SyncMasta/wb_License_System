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

    # ----------------------- Activation-Consents (Pflicht + Optional) ------
    contact_email = fields.Char(
        string='Email-Adresse',
        required=True,
        default=lambda self: self.env.user.email or '',
        help="Email-Adresse für Activate-Audit und ggf. Newsletter-Anmeldung.",
    )
    confirm_eula = fields.Boolean(
        string='EULA bestätigen',
        help="Pflicht. Ich habe die End-User-License-Agreement (EULA) "
             "gelesen und akzeptiert.",
    )
    confirm_terms = fields.Boolean(
        string='AGB bestätigen',
        help="Pflicht. Ich akzeptiere die Allgemeinen Geschäftsbedingungen "
             "(AGB) von WISSEN BERATUNG.",
    )
    confirm_privacy = fields.Boolean(
        string='Datenschutzhinweis bestätigen',
        help="Pflicht. Ich habe den Datenschutzhinweis (DSGVO) zur Kenntnis "
             "genommen.",
    )
    confirm_no_refund = fields.Boolean(
        string='Verzicht auf Gutschrift bestätigen',
        help="Pflicht. Ich bestätige, dass mit der Aktivierung der Lizenz "
             "eine spätere Gutschrift bzw. Erstattung des Kaufpreises "
             "ausgeschlossen ist.",
    )
    subscribe_newsletter = fields.Boolean(
        string='Newsletter abonnieren',
        help="Optional. Ich möchte über Updates, Sicherheits-Patches und "
             "neue Features dieses Moduls per Email informiert werden.",
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
        email = (self.contact_email or '').strip()

        # Pflicht-Consents lokal prüfen — bricht früh ab statt einen
        # Server-Roundtrip zu verschwenden. Server validiert nochmal.
        missing = []
        if not self.confirm_eula:
            missing.append(_('EULA'))
        if not self.confirm_terms:
            missing.append(_('AGB'))
        if not self.confirm_privacy:
            missing.append(_('Datenschutzhinweis'))
        if not self.confirm_no_refund:
            missing.append(_('Verzicht auf Gutschrift'))
        if missing:
            raise UserError(_(
                "Bitte bestätigen Sie folgende Pflicht-Punkte, um die Lizenz "
                "aktivieren zu können:\n\n• %s"
            ) % '\n• '.join(missing))
        if not email:
            raise UserError(_("Bitte eine Email-Adresse angeben."))

        consents = {
            'confirm_eula': True,
            'confirm_terms': True,
            'confirm_privacy': True,
            'confirm_no_refund': True,
            'subscribe_newsletter': bool(self.subscribe_newsletter),
            'contact_email': email,
        }

        info = self.env['wb.license.client'].activate_license(
            product_code, key, code, consents=consents,
        )

        suffix = ''
        if self.subscribe_newsletter:
            suffix = _("\nSie wurden in den Produkt-Newsletter eingetragen.")

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'title': _("Lizenz aktiviert"),
                'message': _(
                    "Die Lizenz für %s ist jetzt aktiv. Gültig bis %s.%s"
                ) % (product_code, info.valid_to or '-', suffix),
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
