=======================================
WB Subscription & License Platform
=======================================

:Status: Sprint 1–8 abgeschlossen (v19.0.1.0.0) — Funktional komplett, vor Produktion smoke-testen
:License: OPL-1
:Author: WISSEN BERATUNG (Tobias Wissen)

Produkt-agnostisches Vertriebs-Backend für WISSEN BERATUNG Odoo-Module.

Funktionsumfang (Sprint 1–8)
=============================

**Datenmodelle**
  ``wb.license.key`` (State-Machine mit 7 States), ``wb.license.event`` (Audit-Log),
  ``wb.license.tag``, ``wb.activation.ticket`` (Fernet-encrypted, IP-binding),
  ``wb.license.migration.request`` (Approve/Reject/Cancel), ``wb.license.trial.request``,
  ``wb.notification.log``, ``wb.rate.limit.entry``, ``wb.dashboard``

**Kryptographie**
  bcrypt für Activation-Codes (12 Runden), Fernet für Ticket-Code-Storage,
  SHA256-Fingerprint für Instanz-Bindung, Auto-Generate Fernet-Key beim Install

**HTTP-API**
  ``/api/license/check|activate|migrate`` (CORS *), ``/api/license/trial``,
  ``/api/wb_subscription/order`` (X-API-Key, CORS *.wissen-beratung.de),
  Portal-Routes ``/activate/<token>`` mit Email-OTP, Public-Verify-Page mit reCAPTCHA v3

**Notifications**
  16 Mail-Templates (Activation, Trial, Renewal, Grace, Expired, Migration, Revoke),
  Telegram-Daily-Summary, lokaler Telegram-Notifier (standalone, kein Coupling
  an wb_odoo_automations)

**PDF-Reports**
  Lizenzzertifikat (weißer Hintergrund, Navy/Cyan-Akzente, QR-Code), EULA-Wrapper,
  Rechnungs-Erweiterung pro Lizenz-Zeile

**Crons**
  License-State-Update (täglich), Activation-Reminders (7d/30d), Renewal-Reminders
  (60d/30d), Ticket-Cleanup, Rate-Limit-Cleanup, Daily-Telegram-Summary,
  Dezember-Renewal-Invoice-Generator (Drafts mit Idempotenz)

**Admin-UI**
  Dashboard mit MRR/ARR/14 KPIs, Pivot/Graph/Kanban auf Lizenzen, Smart-Buttons
  auf Partner (Lizenzen + Migrationen), "Lizenz-Produkte" als gefilterte Action

**Sicherheit (DECISIONS #38–48)**
  Activation-Code nur als bcrypt-Hash, Fernet-Encryption nur temporär im Ticket,
  Burn-on-Consume, IP-Binding bei 3 Mismatches → Revoke, Rate-Limits pro Endpoint,
  reCAPTCHA v3 auf Public-Verify, Anonymisierung Lizenznehmer

**Tests**
  ``test_key_generator.py`` (17 Tests), ``test_license_activation.py`` (10 Tests),
  ``test_ticket_workflow.py`` (11 Tests), ``test_dashboard.py`` (5 Tests)

Was noch offen ist
===================

* Payment-Provider in Odoo konfigurieren (Sales → Configuration →
  Payment Providers). Stripe / SEPA / Sofortüberweisung — egal welcher,
  läuft über den Odoo-Standard-Rechnungs-Flow. wb_subscription enthält
  KEINEN Provider-spezifischen Code.
* EULA-Texte juristisch prüfen lassen (DECISION #37, ~300–600 €)
* Smoke-Test auf s02 mit echtem Webshop-Flow + Telnyx-Modul als Test-Konsument

Bestell- und Zahlungs-Flow
===========================

Der Order-Endpoint legt die Bestellung an, **erzeugt aber noch keine
Lizenz**. Die Lizenz wird erst erzeugt wenn der Zahlungseingang gebucht
ist — folgt dem Odoo-Standard, kein Provider-Coupling.

::

    1. Webseite POST /api/wb_subscription/order
       → res.partner + sale.order (Draft) angelegt
       → Response: order_id + Hinweis "Rechnung folgt per Mail"

    2. Tobias bestätigt Order in Odoo (oder Auto-Confirm-Regel greift)
       → Rechnung-Draft wird erzeugt

    3. Rechnung wird versendet (Standard-Mail mit Payment-Link)
       → Kunde sieht Link zum Customer-Portal von Odoo

    4. Kunde zahlt über den Link
       → account.payment wird angelegt
       → account.move.payment_state ändert sich auf 'paid' (oder 'in_payment')

    5. account.move.write Hook fängt das ab
       → ruft sale.order._wb_issue_license_keys() auf
       → Lizenz + Activation-Code + Ticket + Cert-PDF + 2 Mails + Telegram

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
