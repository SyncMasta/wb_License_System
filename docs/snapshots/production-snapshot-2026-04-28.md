# Production-Snapshot — 2026-04-28

> **Zweck:** Read-only Bestandsaufnahme des Live-Systems `wissen-beratung.de`
> als Foundation für den Security-Hardening-Sprint (Sprint 0-6).
> Snapshot ist Punkt-in-Zeit; nach jeder Major-Migration neu erzeugen.

## Server-Stack

| Bereich | Wert |
|---|---|
| Host | `wissen-beratung.de` (`s02`) |
| OS | Ubuntu 24.04.4 LTS |
| Postgres | 16.13 |
| Odoo | 19, als systemd-Service `odoo19.service`, User `odoo19` |
| Odoo-Workers | 2 + 1 gevent (longpolling 8072) |
| Odoo-Pfad | `/opt/odoo19/` |
| Custom-Addons | `/opt/odoo19/custom_addons/` |
| Config | `/etc/odoo19.conf` (`proxy_mode=True`, `db_password` Klartext) |
| systemd | **kein `EnvironmentFile=`** — relevant für Sprint 2 (ENV-Fernet) |
| DB-Name | `Main` (Capital, case-sensitive) |
| DB-UUID | `c15ad4be-0e2a-11f1-b755-a0a20852785e` |
| DB-Größe | 179 MB |
| `wb_license_client.server_url` | `https://wissen-beratung.de` (Self-Loop) |

## Installierte WB-Module (Stand)

| Modul | Server-Version | Lokale Version | Drift |
|---|---|---|---|
| `wb_anpassungen` | `19.0.0.6` | — | (außer Scope) |
| `wb_bitwarden` | `19.0.1.0.0` | `19.0.1.0.0` | gleich |
| `wb_bitwarden_pro` | `19.0.1.0.1` | `19.0.1.2.0` | **2 Versionen Drift** |
| `wb_license_client` | `19.0.1.0.0` | `19.0.1.1.0` | 1 Version Drift |
| `wb_subscription` | `19.0.1.2.0` | `19.0.1.2.0` | gleich |
| `wb_odoo_automations` | `19.0.0.6.0` | — | (außer Scope) |
| `wb_contact_sync` | — | — | uninstalled |
| `wb_telnyx_phone` | — | — | uninstalled |

→ **Pro-Update-Path** muss vor Sprint-1-Deploy laufen: 1.0.1 → 1.2.0
  bringt das fehlende Modell `wb_bitwarden_partner_collection`.

## Datenbestand (Subscription-Backend)

| Tabelle | Zeilen | Notiz |
|---|---|---|
| `wb_license_key` | **0** | keine ausgegebenen Lizenzen |
| `wb_activation_ticket` | 0 | |
| `wb_license_install` | 1 | nur Self-Test (BITW, vom Server selbst) |
| `wb_rate_limit_entry` | 0 | |
| `wb_license_event` | (n.a.) | nicht abgefragt |
| `wb_bitwarden_account` | 0 | **kein Bitwarden konfiguriert** |
| `wb_bitwarden_partner_collection` | (Tabelle existiert nicht) | wird mit Pro-Update angelegt |

→ **Effektiv pre-launch.** Keine Bestandskunden, keine Migration-Backfills,
  kein Whitelist-Reverse-Engineering nötig. Schema-Änderungen können
  destruktiver/strikter sein als ursprünglich geplant.

## Crypto / Secrets

| Secret | Speicherort | Soll laut Sprint 2 |
|---|---|---|
| `wb_subscription.fernet_key` | `ir_config_parameter` | ENV `WB_SUBSCRIPTION_FERNET_KEY` (mit DB-Fallback) |
| `wb_bitwarden.fernet_key` | `ir_config_parameter` | ENV `WB_BITWARDEN_FERNET_KEY` |
| `wb_telnyx_phone.fernet_key` | `ir_config_parameter` | (außer Scope, aber Pattern-Kandidat) |
| `wb_bitwarden.device_identifier` | `ir_config_parameter` | unverändert (kein Secret) |

## Cleanup-Aktion 2026-04-28

**Verwaiste Credentials entfernt** — Account ID 4 existierte nicht mehr,
seine Credentials lagen verwaist in `ir_config_parameter`:

```sql
DELETE FROM ir_config_parameter
WHERE key LIKE 'wb_bitwarden.cred.4.%';
-- 2 rows: id 303 (client_id), id 304 (client_secret)
```

**Root-Cause:** `wb_bitwarden_account.unlink()` räumt zugehörige
`ir_config_parameter`-Records nicht auf. Wird als Befund **B-N1** in
Sprint 2 mit gefixt (Override `unlink()` + Init-Cleanup-Migration).

## Plan-Auswirkungen (zusammengefasst)

| Sprint | Vor Snapshot | Nach Snapshot |
|---|---|---|
| 0 | Test-Tenant mit Production-Daten-Clone | leerer Self-Test reicht |
| 1 | Cache-Lapse-Risiko bei Bestandskunden | risikolos |
| 2 | Self-Hosted-Cert-Konflikt prüfen | nicht relevant (0 Accounts) |
| 3 | IDOR-Backfill, Whitelist-Migration | Whitelist als Pflichtfeld scharf einziehen |
| 4 | B-M2 Cross-Company-Constraint mit Bestandsdaten-Audit | Constraint sofort scharf |
| 6 | EULA-Update wegen Bestandskunden-UX | entfällt |

## Snapshot-Methode

```bash
# SSH-Recon (read-only)
ssh -i C:\Users\Tobias\.ssh\odoo_wissen root@wissen-beratung.de

# Postgres
sudo -u postgres psql -d Main

# Module-State
SELECT name, latest_version, state, license FROM ir_module_module
WHERE name LIKE 'wb%' OR name LIKE '%bitwarden%' ORDER BY name;

# Lizenz-Bestand
SELECT product_code, state, COUNT(*) FROM wb_license_key
GROUP BY product_code, state ORDER BY product_code, state;
```

Vollständige Recon siehe Conversation-Log 2026-04-28.
