"""Fremde Instanzen einer Lizenz: Test-Datenbanken, Kopien, Umzuege.

Meldet sich ein Key aus einer Datenbank, die nicht seiner Bindung
(``bound_db_uuid`` + ``bound_domain``) entspricht, legt der Check-Endpoint
hier einen Eintrag je ``(license_id, db_uuid)`` an. Die gebundene
Produktivinstanz selbst wird nicht gespeichert.

Regeln (DECISIONS.md #80-82):

* Neutralisierte Kopie (``database.is_neutralized``) bekommt einmalig eine
  Eval ueber ``EVAL_DAYS`` Tage, hoechstens ``MAX_PARALLEL_EVALS`` gleichzeitig
  pro Key. Danach braucht sie eine DEV-Lizenz.
* Eine DEV-Lizenz gilt nur, solange die Datenbank neutralisiert ist.
* Nicht neutralisierte Kopie oder gleiche DB unter neuer Domain: Kulanz ueber
  ``MISMATCH_GRACE_DAYS`` Tage, danach gesperrt (Umzug ueber Migration).

Bewusst ein eigenes Modell und nicht ``wb.license.install``: die
Install-Registry ist eine Lead-Liste mit Retention-Crons. Wuerden deren
Eintraege geloescht, bekaeme dieselbe Kopie eine neue Eval.
"""

from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

EVAL_DAYS = 14
MAX_PARALLEL_EVALS = 2
MISMATCH_GRACE_DAYS = 7
DEV_LICENSE_MONTHS = 12

INSTANCE_STATUS = [
    ('eval', 'Eval laeuft'),
    ('eval_expired', 'Eval abgelaufen'),
    ('eval_limit', 'Eval-Limit erreicht'),
    ('dev', 'DEV-Lizenz'),
    ('dev_not_neutralized', 'DEV-Lizenz, DB nicht neutralisiert'),
    ('mismatch_grace', 'Fremde Instanz (Kulanz)'),
    ('mismatch', 'Fremde Instanz (gesperrt)'),
]

# Status, in denen die Instanz die Produkt-Funktionen nutzen darf.
VALID_STATUS = ('eval', 'dev', 'mismatch_grace')


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

    _sql_constraints = [
        ('license_db_unique', 'UNIQUE(license_id, db_uuid)',
         'Pro Lizenz und Datenbank gibt es nur einen Instanz-Eintrag.'),
    ]

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
        return {'status': status, 'until': until}

    def _classify(self, license, same_db, is_neutralized, today):
        self.ensure_one()
        test = self.dev_license_id
        if test and test.state in ('active', 'grace') and not same_db:
            if not is_neutralized:
                return 'dev_not_neutralized', None
            return 'dev', test.valid_to

        if is_neutralized and not same_db:
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

    def action_issue_dev_license(self):
        """Stellt fuer diese (neutralisierte) Instanz eine DEV-Lizenz aus.

        Die DEV-Lizenz ist ein eigener Key mit Verweis auf die Produktivlizenz,
        gebunden an die db_uuid dieser Instanz. Der Kunde muss nichts eingeben:
        der Check mit dem Produktiv-Key aus der Kopie findet sie hier.
        Abgerechnet wird ueber ein eigenes Produkt per Sale-Order.
        """
        self.ensure_one()
        parent = self.license_id
        if self.dev_license_id and self.dev_license_id.state in ('active', 'grace'):
            raise UserError(_("Fuer diese Instanz gibt es bereits eine aktive DEV-Lizenz."))
        if not self.is_neutralized:
            raise UserError(_(
                "Die Datenbank ist nicht neutralisiert. Eine DEV-Lizenz gilt "
                "nur fuer neutralisierte Datenbanken."))
        if parent.state not in ('active', 'grace'):
            raise UserError(_("Die Produktivlizenz ist nicht aktiv."))
        today = fields.Date.context_today(self)
        test = self.env['wb.license.key'].create([{
            'product_id': parent.product_id.id,
            'partner_id': parent.partner_id.id,
            'company_id': parent.company_id.id,
            'parent_license_id': parent.id,
            'is_nfr': parent.is_nfr,
            'state': 'active',
            'bound_domain': self.domain,
            'bound_db_uuid': self.db_uuid,
            'valid_from': today,
            # Eigene Laufzeit, eigenes Abo: nicht an die Produktivlizenz gekoppelt.
            'valid_to': today + relativedelta(months=DEV_LICENSE_MONTHS),
            'activated_at': fields.Datetime.now(),
            'activation_hash_method': 'dev',
        }])
        self.write({'dev_license_id': test.id, 'status': 'dev'})
        parent._log_instance_event(
            'dev_license_issued', self, details={'dev_license': test.name})
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'wb.license.key',
            'res_id': test.id,
            'view_mode': 'form',
        }
