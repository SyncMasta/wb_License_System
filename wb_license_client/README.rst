==================
WB License Client
==================

:Status: Sprint 2 (v19.0.1.0.0) — Client-Grundlage
:License: OPL-1
:Author: WISSEN BERATUNG (Tobias Wissen)

Basis-Modul für alle WISSEN BERATUNG Produkt-Module beim Kunden.
Pflicht-Dependency von ``wb_telnyx_voip`` und allen zukünftigen WB-Produkt-Modulen.

Was Sprint 2 liefert
=====================

* ``wb.license.client`` — AbstractModel als Service-Klasse für Produkt-Module
* ``wb.license.info`` — persistenter Cache (ein Record pro Produkt × Company)
* ``license_required`` — Decorator für Feature-Gates
* Täglicher Ping-Cron mit Jitter (01:00-05:00 UTC verteilt)
* Activate-Wizard mit Format-Validation (Key + Code)
* Migrate-Wizard für Domain-Umzüge
* Admin-Mail-Templates bei Grace/Expired
* Backend-Banner bei ``grace``/``expired``/``unknown`` (JS-Service)
* Einstellungen unter ``Einstellungen → Allgemeine Einstellungen → WB Lizenzen``

Was **noch NICHT** drin ist
============================

* Onboarding-Wizard bei Install (kommt in späterem Sprint)
* Multi-Company-Support für pro-Company-Keys (erstmal ein Key pro Instanz)

Dependencies
=============

Odoo 19 Enterprise + Standard-Python. **Keine externen Python-Pakete**
auf Kunden-Seite — das Modul kommt mit ``requests`` (Odoo-Standard) aus.

Integration in Produkt-Module
==============================

Manifest-Eintrag
-----------------

::

    'depends': [
        'base',
        'wb_license_client',   # Pflicht für alle WB-Produkt-Module
    ],

Feature-Gates im Code
----------------------

::

    # Option A — Decorator (bevorzugt)
    from odoo.addons.wb_license_client.models.wb_license_client import license_required

    class WbTelnyxVoipConfig(models.Model):
        _inherit = 'wb.telnyx.voip.config'

        @license_required('TELE')
        def action_send_sms(self):
            # Standard-Feature, 30 Tage Offline-Toleranz OK
            ...

        @license_required('TELE', min_cache_age_days=7)
        def action_send_bulk_sms(self):
            # Verursacht externe Kosten — strengere Policy
            ...

    # Option B — Imperativer Check
    def action_expensive(self):
        info = self.env['wb.license.client'].check_license('TELE')
        if not info.is_valid:
            raise UserError(info.user_message)
        # ...

Welche Methoden gaten?
----------------------

Regel: **Nur feature-kritische Operationen**, keine Read-Operations.

* ✅ Gated: XML-Export, PDF-Generation, externe API-Calls, neue Records anlegen
* ❌ Nicht gated: Listen, Forms, Settings — Kunde soll historische Daten
  auch nach Expired noch einsehen können. Sonst gibt's Kündigungen und
  Klagen.

Installation
=============

::

    systemctl stop odoo19
    cd /opt/odoo19/custom_addons/
    tar -xzf /tmp/wb_license_client.tar.gz
    chown -R odoo19:odoo19 wb_license_client/
    sudo -u odoo19 /opt/odoo19/venv/bin/python3.12 /opt/odoo19/odoo-bin \\
      -c /etc/odoo19.conf -i wb_license_client -d Main --stop-after-init
    systemctl start odoo19

Tests
------

::

    sudo -u odoo19 /opt/odoo19/venv/bin/python3.12 /opt/odoo19/odoo-bin \\
      -c /etc/odoo19.conf -d Main \\
      --test-tags wb_license_client --stop-after-init

Architektur
============

Siehe ``ARCHITECTURE.md`` v1.5 und ``docs/modules/wb_license_client.md``
im Workspace-Root.

Known Gotchas
==============

* ``wb.license.info`` ist ``models.Model``, NICHT ``TransientModel``.
  Odoo-Restarts dürfen den Cache nicht invalidieren.
* ``unknown``-State toleriert 30 Tage Offline (DECISION v1.5). Kritische
  Methoden mit externen Kosten sollten ``min_cache_age_days=7`` setzen.
* Cron läuft mit Jitter ± 4h — nicht wundern wenn ``nextcall`` nicht
  exakt auf 01:00 steht.
