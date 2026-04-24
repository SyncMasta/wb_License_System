"""Erweiterungen auf product.template für Lizenz-Produkte.

Auf Template-Ebene (nicht Variant), weil Lizenz-Metadaten über
alle Varianten gleich sind. product.product erbt die Felder automatisch.
"""

import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


PRODUCT_CODE_RE = re.compile(r'^[A-Z]{4}$')


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    wb_is_license_product = fields.Boolean(
        string='Ist Lizenz-Produkt',
        default=False,
        help="Aktiviert wenn dieses Produkt über die WB-Lizenz-Plattform verkauft wird.",
    )
    wb_technical_code = fields.Char(
        string='Technischer Code',
        size=4,
        help="4-stelliger Großbuchstaben-Code, z.B. 'TELE' oder 'TEST'. "
             "Geht in das Public-Key-Format WB-{CODE}-xxx ein.",
    )
    wb_module_technical_name = fields.Char(
        string='Odoo-Modul-Name',
        help="Technischer Name des auszuliefernden Odoo-Moduls, z.B. 'wb_telnyx_voip'.",
    )
    wb_instance_limit = fields.Integer(
        string='Erlaubte Instanzen',
        default=1,
        help="Wie viele Odoo-Instanzen dürfen mit einem Key aktiviert werden.",
    )
    wb_extra_instance_price = fields.Float(
        string='Preis pro Zusatzinstanz',
    )
    wb_trial_days = fields.Integer(
        string='Trial-Tage',
        default=7,
    )
    wb_activation_grace_days = fields.Integer(
        string='Activation-Frist (Tage)',
        default=90,
        help="Tage nach Key-Erzeugung, in denen der Activation-Code noch benutzt werden kann.",
    )
    wb_eula_template_id = fields.Many2one(
        'mail.template',
        string='EULA-Template',
    )
    wb_certificate_template_id = fields.Many2one(
        'ir.actions.report',
        string='Zertifikat-Report',
    )

    _sql_constraints = [
        ('wb_technical_code_unique',
         'UNIQUE(wb_technical_code)',
         'Technischer Produkt-Code muss einzigartig sein.'),
    ]

    @api.constrains('wb_is_license_product', 'wb_technical_code')
    def _check_wb_license_fields(self):
        for rec in self:
            if rec.wb_is_license_product:
                if not rec.wb_technical_code:
                    raise ValidationError(_(
                        "Lizenz-Produkt '%s' benötigt einen technischen Code (4 Großbuchstaben)."
                    ) % rec.display_name)
                if not PRODUCT_CODE_RE.match(rec.wb_technical_code):
                    raise ValidationError(_(
                        "Technischer Code '%s' muss exakt 4 Großbuchstaben sein (A-Z)."
                    ) % rec.wb_technical_code)


class ProductProduct(models.Model):
    _inherit = 'product.product'

    wb_technical_code = fields.Char(
        related='product_tmpl_id.wb_technical_code',
        store=True,
        index=True,
    )
    wb_is_license_product = fields.Boolean(
        related='product_tmpl_id.wb_is_license_product',
        store=True,
    )
