"""Install-Registry — Lead-Liste der ungekauften Addon-Installationen.

Wenn ein Kunde ein WB-Produkt-Addon installiert ohne Lizenz, sendet der
wb_license_client ein /api/license/announce-Signal. Hier wird der
Eintrag (product_code, domain, db_uuid) angelegt bzw. aktualisiert.

Beim späteren Kauf (bzw. Activate) wird der Eintrag mit der erzeugten
wb.license.key verknüpft und als 'converted' markiert. So bleibt
sichtbar wie lange ein Kunde unlizenziert lief, bevor er gekauft hat.

Sicherheits-/DSGVO-Hinweis:
- Datenfelder: product_code, domain, db_uuid, optional contact_email,
  client_version, ip, user_agent, Zeitstempel.
- Keine Personenbezogenen Daten außer der freiwillig vom Kunden-Admin
  mitgesendeten Email. Opt-out auf Kundenseite via
  ir.config_parameter 'wb_license_client.disable_install_registry'.
"""

import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


INSTALL_STATES = [
    ('unlicensed', 'Unlizenziert (Lead)'),
    ('converted', 'Konvertiert (Lizenz erworben)'),
    ('churned', 'Churned (länger nicht gesehen)'),
]


class WbLicenseInstall(models.Model):
    _name = 'wb.license.install'
    _description = 'WB Addon-Install-Registry (Lead-Liste)'
    _inherit = ['mail.thread']
    _order = 'last_seen_at desc, id desc'
    _rec_name = 'display_name'

    product_code = fields.Char(
        size=4,
        required=True,
        index=True,
        help="4-stelliger Produkt-Code wie der Kunden-Client ihn meldet.",
    )
    product_id = fields.Many2one(
        'product.product',
        string='Produkt',
        compute='_compute_product_id',
        store=True,
        index=True,
        help="Auflösung anhand product.wb_technical_code. Leer wenn der "
             "Code (noch) keinem product.product zugeordnet ist.",
    )
    domain = fields.Char(
        required=True,
        index=True,
        help="web.base.url des Kunden-Odoo zum Zeitpunkt des Announcements.",
    )
    db_uuid = fields.Char(
        required=True,
        index=True,
        help="database.uuid des Kunden-Odoo (stabil über Domain-Wechsel).",
    )

    contact_email = fields.Char(
        string='Kontakt-Email',
        index=True,
        help="Optional. Wenn der Kunde im Client einen Admin-Account "
             "hinterlegt hat, wird dessen Email mitgesendet.",
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Auto-gematchter Partner',
        compute='_compute_partner_id',
        store=True,
        index=True,
        help="res.partner der zur contact_email passt — nur Read-Only-Hinweis.",
    )
    client_version = fields.Char(string='Client-Version')

    first_seen_at = fields.Datetime(
        required=True,
        default=fields.Datetime.now,
        readonly=True,
    )
    last_seen_at = fields.Datetime(
        required=True,
        default=fields.Datetime.now,
        index=True,
    )
    announce_count = fields.Integer(
        default=0,
        help="Wie oft sich diese Install-Instanz schon gemeldet hat.",
    )
    last_seen_ip = fields.Char()
    last_seen_user_agent = fields.Char()

    state = fields.Selection(
        INSTALL_STATES,
        required=True,
        default='unlicensed',
        index=True,
        tracking=True,
    )
    license_id = fields.Many2one(
        'wb.license.key',
        string='Erzeugte Lizenz',
        ondelete='set null',
        readonly=True,
        help="Wird beim Activate automatisch verknüpft.",
    )
    converted_at = fields.Datetime(readonly=True)
    days_until_conversion = fields.Integer(
        compute='_compute_days_until_conversion',
        store=True,
        help="Tage zwischen first_seen_at und converted_at.",
    )

    display_name = fields.Char(compute='_compute_display_name', store=True)

    company_id = fields.Many2one(
        'res.company',
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )

    _sql_constraints = [
        ('install_unique',
         'UNIQUE(product_code, domain, db_uuid)',
         'Pro (Produkt-Code, Domain, DB-UUID) darf es nur einen Install-Eintrag geben.'),
    ]

    @api.depends('product_code', 'domain')
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = f"{rec.product_code or '?'} @ {rec.domain or '?'}"

    @api.depends('product_code')
    def _compute_product_id(self):
        cache = {}
        Product = self.env['product.product'].sudo()
        for rec in self:
            code = rec.product_code or False
            if not code:
                rec.product_id = False
                continue
            if code not in cache:
                cache[code] = Product.search(
                    [('wb_technical_code', '=', code)], limit=1)
            rec.product_id = cache[code] or False

    @api.depends('contact_email')
    def _compute_partner_id(self):
        Partner = self.env['res.partner'].sudo()
        for rec in self:
            if not rec.contact_email:
                rec.partner_id = False
                continue
            rec.partner_id = Partner.search(
                [('email', '=ilike', rec.contact_email)], limit=1) or False

    @api.depends('first_seen_at', 'converted_at')
    def _compute_days_until_conversion(self):
        for rec in self:
            if rec.first_seen_at and rec.converted_at:
                rec.days_until_conversion = (
                    rec.converted_at.date() - rec.first_seen_at.date()
                ).days
            else:
                rec.days_until_conversion = 0

    @api.model
    def announce(self, product_code, domain, db_uuid, **kwargs):
        """Upsert-Einstieg für den /api/license/announce-Endpoint.

        Legt einen neuen Eintrag an oder aktualisiert den bestehenden
        (matched über product_code + domain + db_uuid).

        Bei bereits konvertierten Einträgen wird trotzdem last_seen_at
        aktualisiert (signalisiert dass die Lizenz weiter aktiv läuft),
        aber der State bleibt auf 'converted'.

        :param product_code: 4-Char Produkt-Code (z.B. 'TELE')
        :param domain: web.base.url
        :param db_uuid: database.uuid
        :param kwargs: contact_email, client_version, ip, user_agent
        :return: wb.license.install Record
        """
        if not product_code or not domain or not db_uuid:
            raise ValueError("product_code, domain, db_uuid sind Pflicht.")

        existing = self.sudo().search([
            ('product_code', '=', product_code),
            ('domain', '=', domain),
            ('db_uuid', '=', db_uuid),
        ], limit=1)

        vals = {
            'last_seen_at': fields.Datetime.now(),
            'last_seen_ip': kwargs.get('ip'),
            'last_seen_user_agent': kwargs.get('user_agent'),
        }
        if kwargs.get('contact_email'):
            vals['contact_email'] = kwargs['contact_email']
        if kwargs.get('client_version'):
            vals['client_version'] = kwargs['client_version']

        if existing:
            vals['announce_count'] = existing.announce_count + 1
            existing.sudo().write(vals)
            return existing

        vals.update({
            'product_code': product_code,
            'domain': domain,
            'db_uuid': db_uuid,
            'announce_count': 1,
        })
        return self.sudo().create(vals)

    def mark_converted(self, license):
        """Setzt State auf 'converted' und verknüpft die erzeugte Lizenz.

        Wird aus wb.license.key.activate_with_code aufgerufen wenn ein
        Match (product_code, domain, db_uuid) gefunden wird.
        """
        self.ensure_one()
        if self.state == 'converted' and self.license_id == license:
            return
        self.sudo().write({
            'state': 'converted',
            'license_id': license.id,
            'converted_at': fields.Datetime.now(),
        })

    @api.model
    def _cron_mark_churned(self):
        """Setzt unlicensed Installs ohne Ping seit 60 Tagen auf 'churned'.

        Lead-Hygiene: alte Einträge werden ausgeblendet, bleiben aber
        für Reporting erhalten.
        """
        from datetime import timedelta
        threshold = fields.Datetime.now() - timedelta(days=60)
        stale = self.sudo().search([
            ('state', '=', 'unlicensed'),
            ('last_seen_at', '<', threshold),
        ])
        if stale:
            stale.write({'state': 'churned'})
            _logger.info(
                "[wb_subscription] %d Install-Einträge auf 'churned' gesetzt.",
                len(stale))
