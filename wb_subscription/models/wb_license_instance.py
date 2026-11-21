"""Fremde Instanzen einer Lizenz: Test-Datenbanken, Kopien, Umzuege.

Meldet sich ein Key aus einer Datenbank, die nicht seiner Bindung
(``bound_db_uuid`` + ``bound_domain``) entspricht, legt der Check-Endpoint
hier einen Eintrag je ``(license_id, db_uuid)`` an. Die gebundene
Produktivinstanz selbst wird nicht gespeichert.

Regeln (DECISIONS.md #80-82):

* Neutralisierte Kopie (``database.is_neutralized``) bekommt einmalig eine
  Eval ueber ``EVAL_DAYS`` Tage, hoechstens ``MAX_PARALLEL_EVALS`` gleichzeitig
  pro Key. Danach braucht sie eine DEV-Lizenz.
* Eine DEV-Lizenz gilt nur, solange die Datenbank neutralisiert ist. Verkauft
  wird sie per Sale-Order (``action_create_dev_quotation``); der Key entsteht
  beim Zahlungseingang (#91).
* Eine freie DEV-Lizenz (nach einem Umzug ohne Bindung) uebernimmt die
  naechste neutralisierte Kopie automatisch statt einer Eval (#93).
* Wurde die DEV-Lizenz in eine Produktivlizenz umgewandelt, beantwortet der
  Check die Kopie mit dieser Produktivlizenz (Status ``production``, #92).
* Nicht neutralisierte Kopie oder gleiche DB unter neuer Domain: Kulanz ueber
  ``MISMATCH_GRACE_DAYS`` Tage, danach gesperrt (Umzug ueber Migration).

Bewusst ein eigenes Modell und nicht ``wb.license.install``: die
Install-Registry ist eine Lead-Liste mit Retention-Crons. Wuerden deren
Eintraege geloescht, bekaeme dieselbe Kopie eine neue Eval.
"""

from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

EVAL_DAYS = 14
MAX_PARALLEL_EVALS = 2
MISMATCH_GRACE_DAYS = 7

INSTANCE_STATUS = [
    ('eval', 'Eval laeuft'),
    ('eval_expired', 'Eval abgelaufen'),
    ('eval_limit', 'Eval-Limit erreicht'),
    ('dev', 'DEV-Lizenz'),
    ('dev_not_neutralized', 'DEV-Lizenz, DB nicht neutralisiert'),
    ('production', 'Eigene Produktivlizenz (umgewandelt)'),
    ('mismatch_grace', 'Fremde Instanz (Kulanz)'),
    ('mismatch', 'Fremde Instanz (gesperrt)'),
]

# Status, in denen die Instanz die Produkt-Funktionen nutzen darf.
VALID_STATUS = ('eval', 'dev', 'production', 'mismatch_grace')


class WbLicenseInstance(models.Model):
    _name = 'wb.license.instance'
    _description = 'WB Lizenz: fremde Instanz (Test-DB, Kopie, Umzug)'
    _order = 'last_seen_at desc, id desc'

    license_id = fields.Many2one(
        'wb.license.key', string='Lizenz',
        required=True, ondelete='cascade', index=True,
    )
    product_code = fields.Char(related='license_id.product_code', store=True)
    partner_id = fields.Many2one(related='license_id.partner_id', store=True)
    domain = fields.Char(help="web.base.url der Instanz beim letzten Ping.")
    db_uuid = fields.Char(required=True, index=True)
    is_neutralized = fields.Boolean(
        string='Neutralisiert',
        help="database.is_neutralized beim letzten Ping.",
    )
    status = fields.Selection(INSTANCE_STATUS, index=True, readonly=True)
    first_seen_at = fields.Datetime(default=fields.Datetime.now, readonly=True)
    last_seen_at = fields.Datetime(default=fields.Datetime.now, readonly=True)
    eval_until = fields.Date(
        readonly=True,
        help="Ende der Eval. Einmal gesetzt, wird sie fuer diese DB nie neu vergeben.",
    )
    mismatch_until = fields.Date(
        string='Kulanz bis', readonly=True,
        help="Ende der Kulanz fuer eine nicht neutralisierte fremde Instanz.",
    )
    dev_license_id = fields.Many2one(
        'wb.license.key', string='DEV-Lizenz', readonly=True, ondelete='set null',
    )

    _license_db_unique = models.Constraint(
        'UNIQUE(license_id, db_uuid)',
        'Pro Lizenz und Datenbank gibt es nur einen Instanz-Eintrag.',
    )

    @api.model
    def _resolve(self, license, domain, db_uuid, is_neutralized):
        """Bewertet eine fremde Instanz und gibt den Status-Payload zurueck.

        :return: dict mit ``status`` und ``until`` (date oder None)
        """
        now = fields.Datetime.now()
        today = fields.Date.context_today(self)
        inst = self.sudo().search([
            ('license_id', '=', license.id),
            ('db_uuid', '=', db_uuid),
        ], limit=1)
        if not inst:
            inst = self.sudo().create({
                'license_id': license.id,
                'db_uuid': db_uuid,
                'domain': domain,
            })
        inst.write({
            'domain': domain or inst.domain,
            'is_neutralized': is_neutralized,
            'last_seen_at': now,
        })

        same_db = db_uuid == license.bound_db_uuid
        status, until = inst._classify(license, same_db, is_neutralized, today)
        if inst.status != status:
            inst.status = status
        result = {'status': status, 'until': until}
        if status == 'production':
            prod = inst.dev_license_id.converted_to_license_id
            result['state'] = prod.state
            result['grace_until'] = prod.grace_until if prod.state == 'grace' else None
        return result

    def _classify(self, license, same_db, is_neutralized, today):
        self.ensure_one()
        dev = self.dev_license_id
        if dev and not same_db:
            prod = dev.converted_to_license_id
            if (prod and prod.state in ('active', 'grace')
                    and prod.bound_db_uuid == self.db_uuid):
                return 'production', prod.valid_to
            if dev.state in ('active', 'grace') and dev.bound_db_uuid == self.db_uuid:
                if not is_neutralized:
                    return 'dev_not_neutralized', None
                return 'dev', dev.valid_to

        if is_neutralized and not same_db:
            claimed = self._claim_free_dev_license(license)
            if claimed:
                return 'dev', claimed.valid_to
            if not self.eval_until:
                running = self.search_count([
                    ('license_id', '=', license.id),
                    ('eval_until', '>=', today),
                    ('id', '!=', self.id),
                ])
                if running >= MAX_PARALLEL_EVALS:
                    return 'eval_limit', None
                self.eval_until = today + timedelta(days=EVAL_DAYS)
                license._log_instance_event('instance_eval_started', self)
            if self.eval_until >= today:
                return 'eval', self.eval_until
            return 'eval_expired', None

        if not self.mismatch_until:
            self.mismatch_until = today + timedelta(days=MISMATCH_GRACE_DAYS)
            license._log_instance_event('instance_mismatch', self)
            license._send_telegram(
                "⚠️ Fremde Instanz fuer {key} ({partner}): {domain}, "
                "nicht neutralisiert. Kulanz bis {until}.",
                key=license.name, partner=license.partner_id.name or '',
                domain=self.domain or self.db_uuid, until=self.mismatch_until,
            )
        if self.mismatch_until >= today:
            return 'mismatch_grace', self.mismatch_until
        return 'mismatch', None

    def _claim_free_dev_license(self, license):
        """Bindet eine freie DEV-Lizenz von ``license`` an diese Instanz.

        Frei wird eine DEV-Lizenz, wenn ihre DB per Migration zur neuen
        Produktiv-DB wird (``wb.license.migration.request``). Sie behaelt ihre
        Restlaufzeit und gilt erst wieder, wenn eine neutralisierte Kopie sie
        hier uebernimmt (DECISIONS.md #93).
        """
        self.ensure_one()
        free = self.env['wb.license.key'].sudo().search([
            ('parent_license_id', '=', license.id),
            ('state', 'in', ('active', 'grace')),
            ('bound_db_uuid', '=', False),
            ('converted_to_license_id', '=', False),
        ], order='valid_to desc', limit=1)
        if not free:
            return free
        free.write({'bound_db_uuid': self.db_uuid, 'bound_domain': self.domain})
        self.dev_license_id = free.id
        license._log_instance_event(
            'dev_license_claimed', self, details={'dev_license': free.name})
        license._send_telegram(
            "🔁 Freie DEV-Lizenz {dev} ({partner}) an {domain} gebunden.",
            dev=free.name, partner=license.partner_id.name or '',
            domain=self.domain or self.db_uuid,
        )
        return free

    def _check_dev_license_possible(self):
        self.ensure_one()
        dev = self.dev_license_id
        if dev and dev.state in ('active', 'grace') and dev.bound_db_uuid == self.db_uuid:
            raise UserError(_("Fuer diese Instanz gibt es bereits eine aktive DEV-Lizenz."))
        if not self.is_neutralized:
            raise UserError(_(
                "Die Datenbank ist nicht neutralisiert. Eine DEV-Lizenz gilt "
                "nur fuer neutralisierte Datenbanken."))
        if self.license_id.state not in ('active', 'grace'):
            raise UserError(_("Die Produktivlizenz ist nicht aktiv."))

    def _dev_product(self):
        """Das DEV-Produkt zum Produkt der Produktivlizenz."""
        self.ensure_one()
        tmpl = self.license_id.product_id.product_tmpl_id
        dev = tmpl.wb_dev_tmpl_ids.filtered('active')
        if len(dev) != 1:
            raise UserError(_(
                "Zum Produkt %(product)s gibt es %(count)s DEV-Produkte. Lege genau "
                "eines an (Feld „DEV-Lizenz für“ am Produkt).",
                product=tmpl.display_name, count=len(dev)))
        return dev.product_variant_id

    def action_create_dev_quotation(self):
        """Legt ein Angebot fuer eine DEV-Lizenz dieser Instanz an.

        Der Key entsteht erst beim Zahlungseingang (``sale.order.
        _wb_issue_license_keys``), gebunden an die db_uuid dieser Instanz.
        Ein Aktivierungscode ist nicht noetig: die DB ist ueber den Ping mit
        dem Produktiv-Key bekannt, und die Bindung legt WB fest (#91).
        """
        self.ensure_one()
        self._check_dev_license_possible()
        product = self._dev_product()
        order = self.env['sale.order'].create({
            'partner_id': self.license_id.partner_id.id,
            'company_id': self.license_id.company_id.id,
            'order_line': [(0, 0, {
                'product_id': product.id,
                'product_uom_qty': 1,
                'wb_license_instance_id': self.id,
            })],
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'sale.order',
            'res_id': order.id,
            'view_mode': 'form',
        }

    def _issue_dev_license(self, order):
        """Erzeugt die DEV-Lizenz nach Zahlungseingang der DEV-Order.

        Laufzeit wie jede Lizenz bis 31.12. (#4, #88), Verlaengerung ueber
        die Order (Renewal-Cron).
        """
        self.ensure_one()
        parent = self.license_id
        dev = self.env['wb.license.key'].sudo().create([{
            'product_id': parent.product_id.id,
            'partner_id': parent.partner_id.id,
            'company_id': parent.company_id.id,
            'sale_order_id': order.id,
            'parent_license_id': parent.id,
            'state': 'active',
            'bound_domain': self.domain,
            'bound_db_uuid': self.db_uuid,
            'valid_from': fields.Date.context_today(self),
            'valid_to': order._wb_compute_valid_to(),
            'activated_at': fields.Datetime.now(),
            'activation_hash_method': 'dev',
        }])
        self.sudo().write({'dev_license_id': dev.id, 'status': 'dev'})
        parent._log_instance_event(
            'dev_license_issued', self, details={'dev_license': dev.name})
        parent._send_telegram(
            "🧪 DEV-Lizenz {dev} ({partner}) fuer {domain} ausgestellt.",
            dev=dev.name, partner=parent.partner_id.name or '',
            domain=self.domain or self.db_uuid,
        )
        return dev
