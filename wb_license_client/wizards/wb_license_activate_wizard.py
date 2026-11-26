"""Activate-Wizard.

Zwei Wege (Feld ``mode``):

* ``activate``: Kunde gibt Key + Activation-Code ein, Wizard ruft den Server
  auf und speichert bei Erfolg den Key in ir.config_parameter.
* ``trial``: „Start 7-day free trial“ (DECISIONS.md #94). Name, E-Mail und
  Firma gehen erst nach ausdrücklicher Zustimmung an den Lizenzserver, der
  Test-Schlüssel wird gespeichert und ist sofort aktiv.

Format-Validation läuft vor dem HTTP-Call (spart Rate-Limit-Trefferquote).
Pflichtfelder hängen vom Weg ab und werden deshalb in der Ansicht und in den
Aktionen geprüft, nicht am Feld.
"""

import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


# 4 alphanumerische Großbuchstaben/Ziffern für den Produkt-Code (TELE, MCP1, …).
KEY_FORMAT_RE = re.compile(r'^WB-([A-Z0-9]{4})-([a-f0-9]{8})([A-Z2-7]{2})$')
CODE_FORMAT_RE = re.compile(r'^[A-HJ-NP-Z2-9]{5}(-[A-HJ-NP-Z2-9]{5}){4}$')
EMAIL_RE = re.compile(r'^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$')
PRIVACY_URL = 'https://wissen-beratung.de/legal/datenschutz'


class WbLicenseActivateWizard(models.TransientModel):
    _name = 'wb.license.activate.wizard'
    _description = 'License Activation Wizard'

    mode = fields.Selection(
        [('activate', 'I have a license key'), ('trial', 'Start 7-day free trial')],
        string='How do you want to start?',
        required=True,
        default='activate',
    )
    product_code = fields.Char(
        size=4,
        required=True,
        help="4-character alphanumeric product code (A-Z, 0-9), "
             "e.g. 'TELE' or 'MCP1'. Shown on the license certificate.",
    )
    key = fields.Char(
        string='License Key',
        help="Format: WB-XXXX-xxxxxxxxXX",
    )
    activation_code = fields.Char(
        string='Activation Code',
        help="25 characters in groups of 5, from the portal download.",
    )
    api_secret = fields.Char(
        string='API Secret (optional)',
        help="Signs the requests of this instance to the license server "
             "(format WBS-...). Issued by WISSEN BERATUNG together with the "
             "key. Leave empty if you have none; the license check also works "
             "unsigned.",
    )

    # ----------------------- Activation-Consents (Pflicht + Optional) ------
    contact_email = fields.Char(
        string='Email Address',
        default=lambda self: self.env.user.email or '',
        help="Email address for the activation record and, if chosen, the newsletter.",
    )
    confirm_eula = fields.Boolean(
        string='Accept EULA',
        help="Required. I have read and accept the End User License Agreement (EULA).",
    )
    confirm_terms = fields.Boolean(
        string='Accept Terms and Conditions',
        help="Required. I accept the general terms and conditions of WISSEN BERATUNG.",
    )
    confirm_privacy = fields.Boolean(
        string='Acknowledge Privacy Notice',
        help="Required. I have taken note of the privacy notice (GDPR).",
    )
    confirm_no_refund = fields.Boolean(
        string='Confirm Waiver of Refund',
        help="Required. I confirm that activating the license excludes a later "
             "credit note or refund of the purchase price.",
    )
    subscribe_newsletter = fields.Boolean(
        string='Subscribe to Newsletter',
        help="Optional. I want to be informed by email about updates, security "
             "patches and new features of this module.",
    )

    # ----------------------- Trial (Test per Klick) -------------------------
    trial_contact_name = fields.Char(
        string='Your Name',
        default=lambda self: self.env.user.name or '',
    )
    trial_contact_email = fields.Char(
        string='Your Email',
        default=lambda self: self.env.user.email or self.env.company.email or '',
    )
    trial_company_name = fields.Char(
        string='Company',
        default=lambda self: self.env.company.name or '',
    )
    trial_domain = fields.Char(
        string='Odoo Address',
        readonly=True,
        default=lambda self: self.env['wb.license.client']._get_domain() or '',
    )
    trial_consent = fields.Boolean(
        string='I agree to the transfer of this data',
        help="Required for the trial. Name, email, company, the address of this "
             "Odoo and its database ID are sent to WISSEN BERATUNG to issue and "
             "bind the trial key.",
    )

    info_message = fields.Html(compute='_compute_info_message')

    @api.depends('product_code')
    def _compute_info_message(self):
        for rec in self:
            rec.info_message = _(
                "<p><strong>How to activate your license:</strong></p>"
                "<ol>"
                "<li>Enter the license key (from the certificate)</li>"
                "<li>Click the ticket link in the activation email</li>"
                "<li>Enter the 6-digit confirmation code (email)</li>"
                "<li>Copy the 25-character activation code from the portal "
                "and paste it below</li>"
                "</ol>"
            )

    @api.constrains('key')
    def _check_key_format(self):
        for rec in self:
            if rec.key and not KEY_FORMAT_RE.match(rec.key.strip()):
                raise ValidationError(_(
                    "The license key has an invalid format. "
                    "Expected: WB-XXXX-xxxxxxxxXX (example: WB-TELE-a3f28c919K)"
                ))

    @api.constrains('activation_code')
    def _check_code_format(self):
        for rec in self:
            if rec.activation_code and not CODE_FORMAT_RE.match(rec.activation_code.strip()):
                raise ValidationError(_(
                    "The activation code has an invalid format. "
                    "Expected: 5 groups of 5 characters separated by hyphens "
                    "(example: 7H3K9-M4P2N-RQ8T2-W5X7Y-A9B3F)"
                ))

    @api.constrains('product_code')
    def _check_product_code(self):
        for rec in self:
            if rec.product_code and not re.match(r'^[A-Z0-9]{4}$', rec.product_code):
                raise ValidationError(_(
                    "The product code must be exactly 4 characters from A-Z/0-9."
                ))

    def action_activate(self):
        self.ensure_one()
        key = (self.key or '').strip()
        code = (self.activation_code or '').strip()
        product_code = (self.product_code or '').strip().upper()
        email = (self.contact_email or '').strip()

        if not key or not code:
            raise UserError(_("Please enter the license key and the activation code."))

        # Pflicht-Consents lokal prüfen — bricht früh ab statt einen
        # Server-Roundtrip zu verschwenden. Server validiert nochmal.
        missing = []
        if not self.confirm_eula:
            missing.append(_('EULA'))
        if not self.confirm_terms:
            missing.append(_('Terms and conditions'))
        if not self.confirm_privacy:
            missing.append(_('Privacy notice'))
        if not self.confirm_no_refund:
            missing.append(_('Waiver of refund'))
        if missing:
            raise UserError(_(
                "Please confirm the following required items to activate the "
                "license:\n\n• %s"
            ) % '\n• '.join(missing))
        if not email:
            raise UserError(_("Please enter an email address."))

        consents = {
            'confirm_eula': True,
            'confirm_terms': True,
            'confirm_privacy': True,
            'confirm_no_refund': True,
            'subscribe_newsletter': bool(self.subscribe_newsletter),
            'contact_email': email,
        }

        # Secret VOR dem Activate ablegen: der Server kann so eingerichtet
        # sein, dass er signierte Requests verlangt — dann muss schon der
        # erste Folge-Ping signiert rausgehen.
        secret = (self.api_secret or '').strip()
        if secret:
            self.env['wb.license.client']._store_secret(product_code, secret)

        try:
            info = self.env['wb.license.client'].activate_license(
                product_code, key, code, consents=consents,
            )
        except Exception:
            # Aktivierung gescheitert: ein gerade eingetragenes Secret wieder
            # entfernen, damit kein halber Zustand zurueckbleibt.
            if secret:
                self.env['wb.license.client']._store_secret(product_code, '')
            raise

        suffix = ''
        if self.subscribe_newsletter:
            suffix = _("\nYou have been subscribed to the product newsletter.")

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'title': _("License activated"),
                'message': _(
                    "The license for %(code)s is now active. Valid until %(until)s.%(suffix)s"
                ) % {'code': product_code, 'until': info.valid_to or '-', 'suffix': suffix},
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def action_start_trial(self):
        """Request the free trial (only with consent) and activate its key."""
        self.ensure_one()
        product_code = (self.product_code or '').strip().upper()
        name = (self.trial_contact_name or '').strip()
        email = (self.trial_contact_email or '').strip()
        company = (self.trial_company_name or '').strip()

        # Zustimmung zuerst: ohne sie geht kein personenbezogenes Datum raus.
        if not self.trial_consent:
            raise UserError(_(
                "Please agree to the transfer of your data to WISSEN BERATUNG; "
                "without it no trial can be started."))
        if not name or not company:
            raise UserError(_("Please enter your name and your company."))
        if not EMAIL_RE.match(email):
            raise UserError(_("Please enter a valid email address."))

        result, info = self.env['wb.license.client'].start_trial(
            product_code, name, email, company)
        if result == 'pending':
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'type': 'warning',
                    'title': _("Trial request received"),
                    'message': _(
                        "Your trial request for %s is being reviewed by WISSEN BERATUNG. "
                        "You will receive an email; then click “Start 7-day free trial” "
                        "again with the same email address to activate the key."
                    ) % product_code,
                    'sticky': True,
                    'next': {'type': 'ir.actions.act_window_close'},
                },
            }
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'title': _("Free trial started"),
                'message': _(
                    "The free trial of %(code)s is active until %(until)s."
                ) % {'code': product_code, 'until': info.valid_to or '-'},
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
