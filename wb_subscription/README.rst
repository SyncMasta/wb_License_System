=======================================
WB Subscription & License Platform
=======================================

:Status: Sprint 1 (v19.0.1.0.0) — Core-Grundlage, nicht für Produktion
:License: OPL-1
:Author: WISSEN BERATUNG (Tobias Wissen)

Produkt-agnostisches Vertriebs-Backend für WISSEN BERATUNG Odoo-Module.

Was Sprint 1 liefert
=====================

* Datenmodelle: ``wb.license.key``, ``wb.license.event``, ``wb.license.tag``,
  ``wb.activation.ticket``, ``wb.license.migration.request``,
  ``wb.license.trial.request``, ``wb.notification.log``, ``wb.rate.limit.entry``
* Kryptographie-Helfer: ``wb.key.generator`` (Key, Code, bcrypt, Fernet, Ticket, OTP, Fingerprint)
* Erweiterungen auf ``product.template``, ``sale.subscription``, ``res.partner``
* Admin-UI: Listen, Form, Menu, Smart-Button auf Partner
* Security: Gruppen ``user`` + ``manager``, Record-Rules, ACL
* Tests: ``test_key_generator.py``

Was **noch NICHT** drin ist (kommt in späteren Sprints)
========================================================

* HTTP-Controllers (``/api/license/check``, ``/api/license/activate``, Portal-Routes)
* Mail-Templates + Cron-Jobs (Sprint 4)
* PDF-Reports: Zertifikat, EULA, Rechnungs-Erweiterung (Sprint 3)
* Trial-Form-Controller + reCAPTCHA (Sprint 5)
* Admin-Dashboard mit MRR/ARR (Sprint 6)
* Public-Verify-Page (Sprint 7)
* Migration-Wizard-UI im Client-Modul (Sprint 8)

Dependencies
=============

* Odoo 19 Enterprise (nutzt ``sale_subscription`` und ``account_followup``)
* Python: ``bcrypt``, ``cryptography``

Installation
=============

Voraussetzungen
---------------

``bcrypt`` und ``cryptography`` müssen im Odoo-Python-Environment installiert sein::

    sudo -u odoo19 /opt/odoo19/venv/bin/pip install bcrypt cryptography

Fernet-Key — automatisch beim Install
-------------------------------------

Das Modul generiert beim **ersten Install** automatisch einen Fernet-Key
und speichert ihn in ``ir.config_parameter`` unter dem Key
``wb_subscription.fernet_key``. Kein manueller Setup-Schritt nötig.

Der Hook ist **idempotent**: bei Re-Install oder Update wird der Key nicht
überschrieben. Ein Key-Wechsel würde alle noch offenen Activation-Tickets
entwerten.

High-Security-Option (empfohlen für Produktion)
-----------------------------------------------

Für höhere Sicherheit sollte der Fernet-Key in einer ENV-Variable liegen
statt in ``ir.config_parameter`` — ENV-Variablen landen nicht im DB-Backup
und sind damit besser gegen "DB-Leak kompromittiert alle Tickets" geschützt.

Migration nach ENV::

    # 1. Aktuellen Key aus der DB exportieren (als Manager):
    #    Admin → Technical → Parameters → System Parameters
    #    → wb_subscription.fernet_key → Value kopieren

    # 2. In /etc/systemd/system/odoo19.service ergänzen:
    #    [Service]
    #    Environment="WB_SUBSCRIPTION_FERNET_KEY=<kopierter_key>"

    # 3. Daemon-Reload und Odoo neu starten:
    systemctl daemon-reload
    systemctl restart odoo19

    # 4. Parameter aus DB löschen (sobald verifiziert dass ENV funktioniert):
    #    Admin → Technical → Parameters → System Parameters
    #    → wb_subscription.fernet_key → Löschen

**NIEMALS** den Fernet-Key in Git committen, **NIEMALS** loggen.
Bei vermutetem Leak: Key rotieren und alle Tickets mit state != 'consumed' revoken.

Modul installieren
------------------

::

    systemctl stop odoo19
    cd /opt/odoo19/custom_addons/
    # (tar.gz entpacken)
    chown -R odoo19:odoo19 wb_subscription/
    sudo -u odoo19 /opt/odoo19/venv/bin/python3.12 /opt/odoo19/odoo-bin \\
      -c /etc/odoo19.conf -i wb_subscription -d Main --stop-after-init
    systemctl start odoo19

Tests laufen lassen
-------------------

::

    sudo -u odoo19 /opt/odoo19/venv/bin/python3.12 /opt/odoo19/odoo-bin \\
      -c /etc/odoo19.conf -d Main --test-tags wb_key_generator --stop-after-init

Architektur
============

Siehe ``ARCHITECTURE.md`` v1.5 im Workspace-Root.

Sicherheits-Hinweise
=====================

Vor Produktions-Einsatz:

* ``bcrypt`` und ``cryptography`` in Odoo-Venv installieren
* Fernet-Key aus ``WB_SUBSCRIPTION_FERNET_KEY`` ENV-Variable laden
* Rate-Limits in ``ir.config_parameter`` pflegen
  (``wb_subscription.rate_limit_activate`` etc.)
* HTTPS zwingend (Activation-Codes im Klartext über Draht)
* EULA vom Anwalt prüfen lassen

Siehe ``docs/guides/security.md`` für Details.
