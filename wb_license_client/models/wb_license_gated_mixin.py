# -*- coding: utf-8 -*-
"""License-Gate Mixin — DB-Layer-Schutz gegen Schreibvorgänge ohne Lizenz.

Hintergrund (Sprint 1, Security-Review 2026-04)
-----------------------------------------------
Der ``@license_required``-Decorator schützt nur einzelne Methoden auf
einer einzelnen Klasse. Bypass-Vektoren:

* ``ir.actions.server`` mit Inline-Python (``model.create({...})``)
* RPC mit ORM-Methoden, die nicht decoriert sind (z.B. ``create``, ``write``)
* XML-Data-Records via Module-Update
* ``self.sudo()`` umgeht den Decorator nicht direkt, aber Code-Pfade,
  die ihn zufällig nicht durchlaufen

Dieses Mixin verschiebt die Lizenz-Prüfung in den ORM-Layer:
``create``/``write``/``unlink`` rufen vor ``super()`` einen License-Check.
Damit gilt der Schutz unabhängig vom Aufruf-Pfad.

Usage
-----
::

    class WbBitwardenSend(models.Model):
        _name = 'wb.bitwarden.send'
        _inherit = ['wb.license.gated.mixin', 'mail.thread']
        _license_product_code = 'BITW'

        # Optional: bei diesen Feldern wird der Gate für ``write()``
        # übersprungen — z.B. damit ein Cron State-Updates auch nach
        # Lizenz-Lapse fortschreiben kann (sonst stille Endlos-Schleife)
        _license_gate_skip_fields_on_write = frozenset({
            'state', 'last_error', 'last_sync_at', 'sync_attempts',
        })

Cron-Code, der nach einem initialen Lizenz-Check viele Records schreibt,
kann den Gate per Context-Flag bewusst skippen::

    info = self.env['wb.license.client'].check_license('BITW')
    if not info.is_valid:
        return  # Cron bricht ab
    sends = self.env['wb.bitwarden.send'].search([...])
    sends.with_context(wb_skip_license_check=True).write({'state': 'sent'})

Wichtig: ``AccessError`` (nicht ``UserError``) — damit
``ir.actions.server``-Inline-Python die Exception nicht stillschweigend
schluckt und der Server-Action-Workflow stoppt.
"""

import logging

from odoo import _, api, models
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)

DEFAULT_MIN_CACHE_AGE_DAYS = 30
SKIP_CONTEXT_KEY = 'wb_skip_license_check'


class WbLicenseGatedMixin(models.AbstractModel):
    _name = 'wb.license.gated.mixin'
    _description = 'License-Gated Mixin (DB-layer license enforcement)'

    # ---- Subclass-Settings -------------------------------------------------

    _license_product_code = None
    _license_min_cache_age_days = DEFAULT_MIN_CACHE_AGE_DAYS

    _license_gate_skip_fields_on_write = frozenset()
    _license_gate_skip_fields_on_create = frozenset()

    # ---- Public API --------------------------------------------------------

    @api.model
    def _check_license_active(self):
        """Wirft ``AccessError`` wenn die Lizenz nicht gültig ist."""
        code = self._license_product_code
        if not code:
            raise AssertionError(
                "%s erbt wb.license.gated.mixin, hat aber kein "
                "_license_product_code gesetzt." % self._name
            )
        info = self.env['wb.license.client'].check_license(
            code, min_cache_age_days=self._license_min_cache_age_days,
        )
        if not info.is_valid:
            raise AccessError(info.user_message or _(
                "Keine gültige Lizenz für Produkt '%(code)s'. "
                "Schreibvorgang auf %(model)s abgelehnt.\n\n"
                "Bitte unter Einstellungen → WB Lizenzen aktivieren oder "
                "support@wissen-beratung.de kontaktieren."
            ) % {'code': code, 'model': self._name})

    def _license_gate_skipped(self):
        """Skip-Bedingungen für den Gate (gemeinsam für create/write/unlink).

        Skipped wird:

        * Wenn ``wb_skip_license_check=True`` im Context — explizites Opt-out
          für Crons, die eingangs schon die Lizenz geprüft haben.
        * Während Module-Install/-Update (``install_mode``, ``module_install``,
          ``update_module``) — sonst können ``post_init_hook``s und
          XML-Data-Records nicht angelegt werden.
        """
        ctx = self.env.context or {}
        if ctx.get(SKIP_CONTEXT_KEY):
            return True
        if ctx.get('install_mode') or ctx.get('module_install') \
                or ctx.get('update_module'):
            return True
        return False

    # ---- ORM-Overrides -----------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        if not self._license_gate_skipped():
            skip_set = self._license_gate_skip_fields_on_create
            if not skip_set or not all(
                set((v or {}).keys()).issubset(skip_set) for v in vals_list
            ):
                self._check_license_active()
        return super().create(vals_list)

    def write(self, vals):
        if not self._license_gate_skipped():
            skip_set = self._license_gate_skip_fields_on_write
            keys = set((vals or {}).keys())
            if not skip_set or not keys.issubset(skip_set):
                self._check_license_active()
        return super().write(vals)

    def unlink(self):
        if not self._license_gate_skipped():
            self._check_license_active()
        return super().unlink()
