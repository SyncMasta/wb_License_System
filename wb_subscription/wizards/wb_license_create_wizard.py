"""Manueller Lizenz-Erzeugungs-Wizard — Test-Lizenzen, Mitarbeiter-Lizenzen,
Spezial-Deals ohne Sale-Order-Workflow.

Use-Cases:
* End-to-End Smoke-Test ohne kompletten Pro-forma-Workflow zu durchlaufen
* Mitarbeiter-Lizenz fuer eigene Bitwarden-Org
* Beta-Tester / Demo-Account
* Spezial-Deal ohne Buchhaltung (nicht-uebliche Geschaeftsmodelle)

Erzeugt analog zu sale.order._wb_issue_license_keys:
* wb.license.key (state='issued')
* wb.activation.ticket mit Fernet-encrypted Code
* optional: Zertifikat-PDF
* optional: 'payment_received' + 'activation_instructions' Mails an Partner
* optional: Telegram-Notify an Tobias

Toggles im Wizard: jede Auto-Aktion ist abschaltbar (z.B. fuer reine
Testlaeufe ohne dass der Test-Kunde Mails bekommt).

Pflichtfelder: partner_id, product_id (mit wb_is_license_product=True).
"""
import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class WbLicenseCreateWizard(models.TransientModel):
    _name = 'wb.license.create.wizard'
    _description = 'Manueller Lizenz-Erzeugungs-Wizard'

    partner_id = fields.Many2one(
        'res.partner', string='Lizenznehmer', required=True,
        help="Partner, der die Lizenz erhaelt. Email muss gepflegt sein "
             "fuer Mail-Versand und Mailing-List-Subscribe.",
    )
    product_id = fields.Many2one(
        'product.product', string='Lizenz-Produkt', required=True,
        domain=[('wb_is_license_product', '=', True)],
        help="Nur Produkte mit wb_is_license_product=True sind auswaehlbar.",
    )
    sale_order_id = fields.Many2one(
        'sale.order', string='Sale-Order (optional)',
        help="Verknuepfung zu einer Sale-Order fuer Audit-Trail. Wenn leer: "
             "Stand-alone-Lizenz ohne Bestellbezug (Mitarbeiter, Beta, Test).",
    )

    valid_from = fields.Date(
        required=True, default=fields.Date.context_today,
        string='Gueltig ab',
    )
    valid_to = fields.Date(
        required=True, default=lambda self: self._default_valid_to(),
        string='Gueltig bis',
    )
    instance_limit = fields.Integer(
        string='Erlaubte Instanzen', default=1,
        help="Wie viele Odoo-Instanzen duerfen mit diesem Key aktiviert werden.",
    )

    send_payment_received_mail = fields.Boolean(
        string='Mail "Kauf bestaetigt" senden', default=True,
        help="Schickt mail_template_payment_received an Partner inkl. "
             "Lizenzschluessel und Zertifikat. Bei reinen Tests deaktivieren.",
    )
    send_activation_mail = fields.Boolean(
        string='Mail "Activation-Anleitung" senden', default=True,
        help="Schickt mail_template_activation_instructions mit Portal-Link "
             "fuer den Activation-Code-Abruf.",
    )
    generate_certificate = fields.Boolean(
        string='Zertifikat-PDF erzeugen', default=True,
    )
    send_telegram = fields.Boolean(
        string='Telegram-Notify an Tobias', default=False,
        help="Default aus, weil bei Test-Lizenzen unerwuenscht. Bei "
             "echten manuellen Lizenzen (Spezial-Deal) auf True setzen.",
    )

    notes = fields.Text(
        string='Notiz (Audit-Trail)',
        help="Wird im Audit-Event 'key_generated' im wb.license.event "
             "gespeichert. z.B. 'Mitarbeiter-Lizenz Q2 2026' oder "
             "'Test-Smoke #14'.",
    )

    @api.model
    def _default_valid_to(self):
        """Default = 31.12. dieses Jahres (oder nächstes wenn < 30 Tage).

        Konsistent mit sale.order._wb_compute_valid_to.
        """
        from datetime import date
        today = fields.Date.context_today(self)
        eoy = date(today.year, 12, 31)
        if (eoy - today).days < 30:
            eoy = date(today.year + 1, 12, 31)
        return eoy

    @api.constrains('valid_from', 'valid_to')
    def _check_validity_range(self):
        for rec in self:
            if rec.valid_from and rec.valid_to and rec.valid_to < rec.valid_from:
                raise UserError(_(
                    "Gueltig-bis darf nicht vor Gueltig-ab liegen."
                ))

    def action_create_license(self):
        """Erzeugt License-Key + Ticket (+ optional Cert + Mails + Telegram)."""
        self.ensure_one()
        if not self.partner_id.email and (
            self.send_payment_received_mail or self.send_activation_mail
        ):
            raise UserError(_(
                "Partner '%s' hat keine Email — Mail-Versand nicht moeglich. "
                "Entweder Email pflegen oder beide Mail-Toggles abschalten."
            ) % self.partner_id.display_name)

        Key = self.env['wb.license.key'].sudo()
        Ticket = self.env['wb.activation.ticket'].sudo()
        gen = self.env['wb.key.generator'].sudo()

        grace_days = self.product_id.wb_activation_grace_days or 90
        activation_code = gen.generate_activation_code()
        activation_hash = gen.hash_activation_code(activation_code)

        key_vals = {
            'product_id': self.product_id.id,
            'partner_id': self.partner_id.id,
            'state': 'issued',
            'valid_from': self.valid_from,
            'valid_to': self.valid_to,
            'instance_limit': self.instance_limit,
            'activation_hash': activation_hash,
            'activation_expires_at':
                fields.Datetime.now() + timedelta(days=grace_days),
        }
        if self.sale_order_id:
            key_vals['sale_order_id'] = self.sale_order_id.id

        key = Key.create(key_vals)

        Ticket.create({
            'license_id': key.id,
            'email': self.partner_id.email or '',
            'encrypted_code': gen.encrypt_code(activation_code),
        })

        if self.notes:
            self.env['wb.license.event'].sudo().log_event(
                key, 'key_generated_manual',
                details={'notes': self.notes},
            )

        cert_attachments = []
        if self.generate_certificate:
            try:
                key.action_generate_certificate_pdf()
                cert_attachments = key.certificate_ids.ids
            except Exception as e:
                _logger.exception(
                    "[wb_subscription] Manueller Cert-Generate fuer %s "
                    "fehlgeschlagen: %s", key.name, e)

        if self.send_payment_received_mail:
            try:
                key._send_template(
                    'wb_subscription.mail_template_payment_received',
                    attachments=cert_attachments,
                )
            except Exception as e:
                _logger.exception(
                    "[wb_subscription] Manueller payment_received-Mail-Versand "
                    "fuer %s fehlgeschlagen: %s", key.name, e)

        if self.send_activation_mail:
            try:
                key._send_template(
                    'wb_subscription.mail_template_activation_instructions')
            except Exception as e:
                _logger.exception(
                    "[wb_subscription] Manueller activation_instructions-Mail-"
                    "Versand fuer %s fehlgeschlagen: %s", key.name, e)

        if self.send_telegram:
            try:
                key._send_telegram(
                    "🔑 Manuelle Lizenz: {partner} — {product} ({key})",
                    partner=self.partner_id.name or '',
                    product=self.product_id.name,
                    key=key.name,
                )
            except Exception:
                pass

        del activation_code

        return {
            'type': 'ir.actions.act_window',
            'name': _('Lizenz erstellt'),
            'res_model': 'wb.license.key',
            'res_id': key.id,
            'view_mode': 'form',
            'target': 'current',
        }
