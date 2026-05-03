"""Erweiterungen auf product.template für Lizenz-Produkte.

Auf Template-Ebene (nicht Variant), weil Lizenz-Metadaten über
alle Varianten gleich sind. product.product erbt die Felder automatisch.
"""

import logging
import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

# 4 alphanumerische Großbuchstaben/Ziffern. Ziffern erlauben Versions-Suffixe
# wie MCP1, MCP2 (Model Context Protocol v1, v2). Einheitliche Länge bleibt bei 4.
PRODUCT_CODE_RE = re.compile(r'^[A-Z0-9]{4}$')


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
        help="4-stelliger alphanumerischer Code (A-Z, 0-9), z.B. 'TELE', 'TEST' "
             "oder 'MCP1'. Geht in das Public-Key-Format WB-{CODE}-xxx ein.",
    )
    wb_module_technical_name = fields.Char(
        string='Odoo-Modul-Name',
        help="Technischer Name des auszuliefernden Odoo-Moduls, z.B. 'wb_telnyx_voip'.",
    )
    wb_release_ids = fields.One2many(
        'wb.product.release', 'product_tmpl_id',
        string='Modul-Releases',
        help="Versionierte Tarballs des Lizenz-Produkts. "
             "Pro Produkt wird genau eine Release als 'is_current' geführt "
             "und vom täglichen Lizenz-Ping ausgeliefert.",
    )
    wb_current_release_id = fields.Many2one(
        'wb.product.release',
        string='Aktuelle Release',
        compute='_compute_wb_current_release_id',
        store=True,
        index=True,
        help="Release mit is_current=True. Source of Truth für "
             "wb_latest_module_version und wb_has_downloadable_tarball.",
    )
    wb_latest_module_version = fields.Char(
        string='Aktuelle Modul-Version',
        compute='_compute_wb_latest_module_version',
        store=True,
        help="Version der aktuellen Release. Wird vom täglichen Lizenz-Ping "
             "an die Kunden ausgeliefert; wenn die installierte Version "
             "kleiner ist, zeigt das Pro-Modul beim Kunden einen "
             "'Update verfügbar'-Hinweis. Leer = keine Update-Anzeige.",
    )
    wb_has_downloadable_tarball = fields.Boolean(
        string='Tarball verfügbar',
        compute='_compute_wb_latest_module_version',
        store=True,
    )

    @api.depends('wb_release_ids.is_current', 'wb_release_ids.state')
    def _compute_wb_current_release_id(self):
        for rec in self:
            current = rec.wb_release_ids.filtered(
                lambda r: r.is_current and r.state == 'published')
            rec.wb_current_release_id = current[:1]

    @api.depends('wb_current_release_id', 'wb_current_release_id.version',
                 'wb_current_release_id.has_attachment')
    def _compute_wb_latest_module_version(self):
        for rec in self:
            release = rec.wb_current_release_id
            rec.wb_latest_module_version = release.version if release else False
            rec.wb_has_downloadable_tarball = bool(
                release and release.has_attachment)
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
    wb_default_subscription_plan_id = fields.Many2one(
        'sale.subscription.plan',
        string='Default-Subscription-Plan',
        help="Standard-Odoo-Subscription-Plan (Recurrence) — bestimmt den "
             "Cron-Rhythmus der Folge-Rechnungen. Wird beim Bestätigen der "
             "Sale Order automatisch gesetzt, wenn die Order ein Lizenz-Produkt "
             "enthält und noch keinen Plan hat. Sollte zum wb_billing_calendar "
             "passen (z.B. Plan='Quarterly' bei wb_billing_calendar='quarterly').",
    )
    wb_mailing_list_id = fields.Many2one(
        'mailing.list',
        string='Newsletter-Liste',
        ondelete='set null',
        copy=False,
        help="Mailingliste fuer Service-Mails (Changelog, Sicherheits-Patches) "
             "an Kunden mit aktiver Lizenz dieses Produkts. Wird beim Anlegen "
             "des Produkts (oder beim Setzen von wb_is_license_product=True) "
             "automatisch erzeugt mit Name '[<TECHNICAL_CODE>] <Produkt-Name>'. "
             "Lizenz-Aktivierung subscribed automatisch, expired/revoked/cancelled "
             "unsubscribed (DSGVO-konform per opt_out=True, Audit-Trail bleibt).",
    )
    wb_billing_calendar = fields.Selection(
        [
            ('monthly',   'Monatlich (1.–letzter Tag des Monats)'),
            ('quarterly', 'Quartalsweise (Q1: Jan-Mär, Q2: Apr-Jun, Q3: Jul-Sep, Q4: Okt-Dez)'),
            ('biannual',  'Halbjährlich (H1: Jan-Jun, H2: Jul-Dez)'),
            ('yearly',    'Jährlich (Jan-Dez)'),
        ],
        string='Abrechnungs-Kalender',
        default='monthly',
        help="Kalender-Anker für die erste Rechnung (anteilig zum Ende der "
             "aktuellen Kalenderperiode). Beispiel: 'quarterly' + "
             "Vertragsbeginn 15.05. → Erstrechnung 15.05.–30.06. (Rest-Q2), "
             "danach Q3 und Q4 jeweils zum 1. zum vollen Quartalspreis. "
             "Vertragsende ist universell der 31.12. (`_wb_compute_valid_to`).",
    )

    _sql_constraints = [
        ('wb_technical_code_unique',
         'UNIQUE(wb_technical_code)',
         'Technischer Produkt-Code muss einzigartig sein.'),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if rec.wb_is_license_product and not rec.wb_mailing_list_id:
                rec._wb_create_mailing_list()
        return records

    def write(self, vals):
        res = super().write(vals)
        # Auto-Create der Mailingliste, wenn Produkt nachtraeglich auf
        # Lizenz-Produkt umgestellt wird oder ein technischer Code dazukommt.
        if 'wb_is_license_product' in vals or 'wb_technical_code' in vals:
            for rec in self:
                if (rec.wb_is_license_product
                        and rec.wb_technical_code
                        and not rec.wb_mailing_list_id):
                    rec._wb_create_mailing_list()
        return res

    def _wb_create_mailing_list(self):
        """Erzeugt eine neue mailing.list mit '[<CODE>] <Name>' und verlinkt sie.

        Idempotent — wird nicht aufgerufen wenn schon eine Liste verlinkt ist.
        Best-effort: Fehler werden geloggt aber nicht weitergeworfen, damit
        ein mass_mailing-Konfigurationsproblem das Produkt-Speichern nicht blockt.
        """
        self.ensure_one()
        if self.wb_mailing_list_id:
            return
        if not self.wb_technical_code:
            _logger.warning(
                "[wb_subscription] Mailingliste fuer %s nicht angelegt — "
                "wb_technical_code fehlt.", self.display_name)
            return
        try:
            mailing_list = self.env['mailing.list'].sudo().create({
                'name': f'[{self.wb_technical_code}] {self.name}',
                'is_public': False,
            })
            self.wb_mailing_list_id = mailing_list.id
            _logger.info(
                "[wb_subscription] Mailingliste %s fuer Produkt %s angelegt.",
                mailing_list.name, self.display_name)
        except Exception as e:
            _logger.exception(
                "[wb_subscription] Mailinglisten-Erzeugung fuer %s "
                "fehlgeschlagen: %s", self.display_name, e)

    @api.constrains('wb_is_license_product', 'wb_technical_code')
    def _check_wb_license_fields(self):
        for rec in self:
            if rec.wb_is_license_product:
                if not rec.wb_technical_code:
                    raise ValidationError(_(
                        "Lizenz-Produkt '%s' benötigt einen technischen Code "
                        "(4 alphanumerische Großbuchstaben/Ziffern)."
                    ) % rec.display_name)
                if not PRODUCT_CODE_RE.match(rec.wb_technical_code):
                    raise ValidationError(_(
                        "Technischer Code '%s' muss exakt 4 Zeichen aus A-Z/0-9 sein."
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
