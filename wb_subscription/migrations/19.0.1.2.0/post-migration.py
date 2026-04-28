"""19.0.1.2.0 — Multi-Version-Releases einführen.

Vorher (19.0.1.1.0): Pro Lizenz-Produkt gab es genau einen Tarball-Anhang
am ``product.template`` (Binary-Feld ``wb_latest_module_tarball``) plus
ein manuell gepflegtes Versions-Char ``wb_latest_module_version``.

Jetzt (19.0.1.2.0): Versionen werden in einem eigenen Modell
``wb.product.release`` geführt; pro Produkt darf eine Release
``is_current=True`` sein und wird über die Lizenz-API ausgeliefert.

Diese Migration zieht den existierenden Tarball jedes Lizenz-Produkts
in eine neue Release um:
  * Liest tmpl_id + version aus product_template via raw SQL (das
    Char-Feld bleibt als computed im Code, daher existiert die Spalte
    weiter, der manuell gepflegte Wert lebt im DB-Snapshot vor Recompute).
  * Findet das ir.attachment mit ``res_field='wb_latest_module_tarball'``.
  * Legt eine ``wb.product.release`` mit state='published',
    is_current=True an.
  * Hängt das Attachment auf die neue Release um (res_model/res_id
    aktualisieren, res_field auf False).

Attachments ohne dazugehörigen Versions-Char werden mit Default-Version
'unknown-1' migriert, damit keine Daten verloren gehen — Tobias kann
dann manuell aufräumen. Ist nur ein Edge-Case bei manuell zerschossenen
Records.
"""

import logging

from odoo import SUPERUSER_ID, api, fields

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})

    if 'wb.product.release' not in env:
        _logger.warning(
            "[wb_subscription] wb.product.release-Modell nicht verfügbar — "
            "Multi-Version-Migration übersprungen.")
        return

    cr.execute("""
        SELECT id, COALESCE(wb_latest_module_version, '')
          FROM product_template
         WHERE wb_is_license_product = TRUE
    """)
    rows = cr.fetchall()
    if not rows:
        _logger.info(
            "[wb_subscription] Keine Lizenz-Produkte gefunden — "
            "Multi-Version-Migration nichts zu tun.")
        return

    Release = env['wb.product.release'].sudo()
    Att = env['ir.attachment'].sudo()
    migrated = 0

    for tmpl_id, version_str in rows:
        attachment = Att.search([
            ('res_model', '=', 'product.template'),
            ('res_id', '=', tmpl_id),
            ('res_field', '=', 'wb_latest_module_tarball'),
        ], limit=1)
        if not attachment:
            continue

        target_version = (version_str or '').strip() or 'unknown-1'

        existing = Release.search([
            ('product_tmpl_id', '=', tmpl_id),
            ('version', '=', target_version),
        ], limit=1)
        if existing:
            _logger.info(
                "[wb_subscription] Release %s für Template %s existiert "
                "bereits — überspringe.", target_version, tmpl_id)
            continue

        release = Release.create({
            'product_tmpl_id': tmpl_id,
            'version': target_version,
            'filename': attachment.name or '',
            'state': 'published',
            'released_at': fields.Datetime.now(),
        })
        attachment.write({
            'res_model': 'wb.product.release',
            'res_id': release.id,
            'res_field': False,
        })
        release.write({'attachment_id': attachment.id})
        release.action_set_current()
        migrated += 1

    if migrated:
        _logger.info(
            "[wb_subscription] %d Lizenz-Produkt-Tarball(s) auf "
            "wb.product.release migriert.", migrated)
