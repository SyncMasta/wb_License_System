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
    ('lead_qualified', 'Lead qualifiziert (Daten erfasst)'),
    ('converted', 'Konvertiert (Lizenz erworben)'),
    ('churned', 'Churned (länger nicht gesehen)'),
]

LEAD_INTENT_SELECTION = [
    ('info', 'Onboarding (nur Daten erfasst)'),
    ('purchase', 'Lizenz-Anfrage (Kaufabsicht)'),
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
    contact_name = fields.Char(
        string='Ansprechpartner',
        help="Vom Kunden-Wizard übermittelter Name (Onboarding/Lizenz-Anfrage).",
    )
    contact_phone = fields.Char(
        string='Telefon',
        help="Vom Kunden-Wizard übermittelte Telefonnummer.",
    )
    company_name = fields.Char(
        string='Firma (Kunde)',
        help="Vom Kunden-Wizard übermittelter Firmenname.",
    )
    company_vat = fields.Char(
        string='USt-IdNr.',
    )
    company_street = fields.Char(string='Straße')
    company_zip = fields.Char(string='PLZ')
    company_city = fields.Char(string='Stadt')
    company_country_code = fields.Char(string='Land (ISO)', size=2)
    lead_notes = fields.Text(
        string='Notizen vom Kunden',
        help="Freitext aus dem Lead-Wizard.",
    )
    lead_intent = fields.Selection(
        LEAD_INTENT_SELECTION,
        string='Lead-Intent',
        help="'info' = Daten beim Onboarding gesammelt, "
             "'purchase' = Kunde hat aktiv eine Lizenz angefragt.",
    )
    crm_lead_id = fields.Many2one(
        'crm.lead',
        string='Erzeugter CRM-Lead',
        ondelete='set null',
        readonly=True,
        help="Verknüpfter Lead in der Sales-Pipeline. Leer wenn crm-Modul "
             "nicht installiert war zum Zeitpunkt des Lead-Eingangs.",
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Auto-gematchter Partner',
        compute='_compute_partner_id',
        store=True,
        index=True,
        help="res.partner der zur contact_email passt — nur Read-Only-Hinweis.",
    )
    client_version = fields.Char(
        string='Client-Version',
        help="Version des wb_license_client-Moduls beim Kunden — Telemetrie "
             "für Lizenz-Schicht selbst (nicht das Produkt-Modul).",
    )
    installed_module_version = fields.Char(
        string='Installierte Modul-Version',
        index=True,
        help="Version des Produkt-Moduls (z.B. wb_bitwarden_pro 19.0.1.1.0) "
             "wie zuletzt vom Kunden gemeldet. Aktualisiert sich bei jedem "
             "/api/license/check-Ping. Vergleich mit "
             "product_id.wb_latest_module_version zeigt Update-Bedarf.",
    )

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

    @api.model
    def submit_lead(self, payload):
        """Upsert + Lead/Opportunity-Erzeugung aus dem Kunden-Wizard.

        Wird vom /api/license/lead-Endpoint aufgerufen. Vereint zwei Flows:

        * **Onboarding** (``intent='info'``): Daten werden im Install-Record
          gespeichert und ein ``crm.lead`` mit ``type='lead'`` erzeugt
          (CRM in Pipeline-Vorstufe).
        * **Lizenz-Anfrage** (``intent='purchase'``): zusätzlich wird der
          State auf ``lead_qualified`` gesetzt und ein ``crm.lead`` mit
          ``type='opportunity'`` (Verkaufschance) erzeugt.

        Falls das ``crm``-Modul nicht installiert ist (weiche Dependency):
        Daten werden nur im Install-Record persistiert, ``crm_lead_id``
        bleibt leer; der Sales-Mailbox-Notify-Hook (siehe Controller)
        übernimmt die Eskalation.

        :param payload: dict aus dem Wizard, siehe Controller-Validierung.
        :return: ``wb.license.install`` Record (gleicher der Upsert-Logik).
        """
        product_code = (payload.get('product_code') or '').strip().upper()
        domain = (payload.get('domain') or '').strip()
        db_uuid = (payload.get('db_uuid') or '').strip()
        intent = payload.get('intent') if payload.get('intent') in ('info', 'purchase') else 'info'

        install = self.sudo().search([
            ('product_code', '=', product_code),
            ('domain', '=', domain),
            ('db_uuid', '=', db_uuid),
        ], limit=1)

        vals = {
            'last_seen_at': fields.Datetime.now(),
            'last_seen_ip': payload.get('_ip'),
            'last_seen_user_agent': payload.get('_user_agent'),
            'contact_email': payload.get('contact_email') or False,
            'contact_name': payload.get('contact_name') or False,
            'contact_phone': payload.get('contact_phone') or False,
            'company_name': payload.get('company_name') or False,
            'company_vat': payload.get('company_vat') or False,
            'company_street': payload.get('company_street') or False,
            'company_zip': payload.get('company_zip') or False,
            'company_city': payload.get('company_city') or False,
            'company_country_code': (payload.get('company_country_code') or '')[:2] or False,
            'lead_notes': payload.get('notes') or False,
            'lead_intent': intent,
            'client_version': payload.get('client_version') or False,
        }
        if intent == 'purchase':
            vals['state'] = 'lead_qualified'

        if install:
            vals['announce_count'] = install.announce_count + 1
            install.sudo().write(vals)
        else:
            vals.update({
                'product_code': product_code,
                'domain': domain,
                'db_uuid': db_uuid,
                'announce_count': 1,
            })
            install = self.sudo().create(vals)

        install._ensure_crm_lead(intent=intent)
        return install

    def _ensure_crm_lead(self, intent='info'):
        """Erzeugt einen crm.lead/Opportunity wenn das CRM-Modul vorhanden ist.

        Idempotent — beim zweiten Aufruf wird ein existierender ``crm_lead_id``
        nicht überschrieben, sondern nur ein Chatter-Log angehängt.
        Bei Eskalation von 'info' → 'purchase' wird der bestehende Lead
        zur Opportunity konvertiert.
        """
        self.ensure_one()
        Lead = self.env.get('crm.lead')
        if Lead is None:
            _logger.info(
                "[wb_subscription] crm-Modul nicht installiert — "
                "kein Lead/Opportunity für install id=%s erzeugt.", self.id)
            return False

        lead_type = 'opportunity' if intent == 'purchase' else 'lead'
        lead_name = '[%s] %s — %s' % (
            self.product_code,
            self.company_name or self.contact_email or self.domain,
            'Lizenz-Anfrage' if intent == 'purchase' else 'Onboarding',
        )
        description_lines = [
            'Quelle: WB Lizenz-Client (%s)' % self.product_code,
            'Domain: %s' % self.domain,
            'DB-UUID: %s' % self.db_uuid,
            'Client-Version: %s' % (self.client_version or '-'),
        ]
        if self.lead_notes:
            description_lines.append('---')
            description_lines.append('Notizen: %s' % self.lead_notes)
        description = '\n'.join(description_lines)

        country = False
        if self.company_country_code:
            country = self.env['res.country'].sudo().search(
                [('code', '=', self.company_country_code.upper())], limit=1)

        lead_vals = {
            'name': lead_name,
            'type': lead_type,
            'partner_name': self.company_name or False,
            'contact_name': self.contact_name or False,
            'email_from': self.contact_email or False,
            'phone': self.contact_phone or False,
            'street': self.company_street or False,
            'zip': self.company_zip or False,
            'city': self.company_city or False,
            'country_id': country.id if country else False,
            'description': description,
            'priority': '2' if intent == 'purchase' else '0',
        }
        if self.partner_id:
            lead_vals['partner_id'] = self.partner_id.id

        if self.crm_lead_id:
            if intent == 'purchase' and self.crm_lead_id.type == 'lead':
                self.crm_lead_id.sudo().write({
                    'type': 'opportunity',
                    'priority': '2',
                    'description': (self.crm_lead_id.description or '') +
                                   '\n\n--- Eskaliert zur Verkaufschance ---\n' + description,
                })
            try:
                self.crm_lead_id.sudo().message_post(
                    body=_("Erneuter Lead-Eingang vom Kunden (intent=%s).") % intent,
                    subtype_xmlid='mail.mt_note',
                )
            except Exception:
                pass
            return self.crm_lead_id

        lead = Lead.sudo().create(lead_vals)
        self.sudo().write({'crm_lead_id': lead.id})
        return lead

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
