"""Customer-Portal-Routes für authentifizierte Modul-Tarball-Downloads.

Flow:
1. GET /my                                          → Standard-Portal-Home, zeigt
                                                      Download-Card mit Counter
                                                      (über portal_my_home-Inherit).
2. GET /my/downloads                                → Liste aller Lizenzen des
                                                      eingeloggten Customers, je
                                                      Lizenz die aktuelle Version
                                                      plus Link auf Versions-History.
3. GET /my/downloads/<int:license_id>               → Versions-History für eine
                                                      Lizenz: alle 'published'
                                                      Releases mit Download-Buttons,
                                                      current-Release oben markiert.
4. GET /my/downloads/<int:license_id>/<int:rel_id>  → Streamt den Tarball der
                                                      angefragten Release;
                                                      validiert Partner-Match,
                                                      Lizenz-State und dass die
                                                      Release published ist.

Sicherheits-Pattern:
- ``auth='user'`` — Customer muss eingeloggt sein. Odoo handelt
  Login-Redirect automatisch.
- Partner-Match via ``commercial_partner_id`` (Stammkunde, nicht
  Kontakt-Sub-Partner).
- Lizenz muss state ∈ {active, grace} sein. Expired/revoked → 404.
- Release muss zum Lizenz-Produkt gehören und state='published' haben,
  archived/draft → 404.
- Audit-Eintrag pro Download in wb.license.event (typ='download') mit
  Versions-Detail.
"""

import logging

from odoo import http
from odoo.http import request
from odoo.addons.portal.controllers.portal import CustomerPortal

_logger = logging.getLogger(__name__)


def _user_can_download(user, license):
    """True wenn ``user`` für diese Lizenz Tarballs herunterladen darf.

    Sprint 3 / L-C4 — schließt Sub-Kontakt-IDOR.

    * Wenn ``license.allowed_portal_user_ids`` GEFÜLLT ist:
      nur diese User dürfen, exakte Liste, kein Fallback.
    * Wenn LEER (Default): nur User mit ``user.partner_id == license.partner_id``
      direkt — Sub-Kontakte mit gleichem ``commercial_partner_id`` haben
      KEINEN Zugriff (war vorher der IDOR-Pfad).

    Tobias kann pro Lizenz explizit weitere User freigeben (z.B. wenn ein
    B2B-Kunde mehrere Mitarbeiter den Tarball laden lassen soll).
    """
    if license.allowed_portal_user_ids:
        return user in license.allowed_portal_user_ids
    return user.partner_id and user.partner_id == license.partner_id


def _published_releases(product_tmpl):
    """Alle published Releases eines Produkts, current zuerst, dann nach Datum."""
    releases = product_tmpl.wb_release_ids.filtered(
        lambda r: r.state == 'published' and r.attachment_id)
    return releases.sorted(
        key=lambda r: (not r.is_current, -(r.released_at.timestamp()
                                          if r.released_at else 0)))


class WbPortalDownloads(CustomerPortal):

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if 'wb_download_count' in counters:
            values['wb_download_count'] = len(self._get_downloadable_licenses())
        return values

    def _get_downloadable_licenses(self):
        """Lizenzen des aktuellen Customers, deren Produkt mindestens eine
        published Release mit Tarball hat.

        Sprint 3 / L-C4: Filtern via ``_user_can_download`` — kein Bulk-
        Match auf ``commercial_partner_id`` mehr (war der IDOR-Pfad).
        """
        user = request.env.user
        if not user or user._is_public():
            return request.env['wb.license.key'].sudo().browse()
        # Kandidaten: Lizenzen wo user direkter Kunde ist ODER user in
        # Whitelist steht. Wir suchen union der beiden Domains.
        Licenses = request.env['wb.license.key'].sudo()
        licenses = Licenses.search([
            ('state', 'in', ('active', 'grace')),
            '|',
              ('partner_id', '=', user.partner_id.id),
              ('allowed_portal_user_ids', 'in', user.id),
        ])
        # Doppelte Sicherheits-Filterung im Code, falls ein Edge-Case
        # ein Match liefert, das _user_can_download trotzdem ablehnen
        # würde (z.B. Whitelist gefüllt + user nicht drin + partner_id
        # passt zufällig nicht).
        licenses = licenses.filtered(
            lambda l: _user_can_download(user, l))
        return licenses.filtered(
            lambda l: bool(_published_releases(l.product_id.product_tmpl_id))
        )

    def _resolve_license(self, license_id):
        """Holt die Lizenz und prüft Whitelist + State.

        Returns ein ``wb.license.key``-Recordset (1 oder leer). Falls
        Zugriff verweigert oder nicht gefunden: leeres Recordset.
        """
        user = request.env.user
        if not user or user._is_public():
            return request.env['wb.license.key'].sudo().browse()
        license = request.env['wb.license.key'].sudo().browse(license_id)
        if not license.exists():
            return request.env['wb.license.key'].sudo().browse()
        if license.state not in ('active', 'grace'):
            return request.env['wb.license.key'].sudo().browse()
        if not _user_can_download(user, license):
            return request.env['wb.license.key'].sudo().browse()
        return license

    @http.route('/my/downloads', type='http', auth='user', website=True)
    def portal_my_downloads(self, **kw):
        licenses = self._get_downloadable_licenses()
        rows = []
        for license in licenses:
            tmpl = license.product_id.product_tmpl_id
            current = tmpl.wb_current_release_id
            rows.append({
                'license': license,
                'product_name': license.product_id.name,
                'current_version': current.version if current else '-',
                'release_count': len(_published_releases(tmpl)),
            })
        return request.render('wb_subscription.portal_my_downloads', {
            'rows': rows,
            'page_name': 'wb_downloads',
        })

    @http.route('/my/downloads/<int:license_id>',
                type='http', auth='user', website=True)
    def portal_license_releases(self, license_id, **kw):
        license = self._resolve_license(license_id)
        if not license:
            return request.not_found()

        tmpl = license.product_id.product_tmpl_id
        releases = _published_releases(tmpl)
        if not releases:
            return request.not_found()

        return request.render('wb_subscription.portal_license_releases', {
            'license': license,
            'product_name': license.product_id.name,
            'releases': releases,
            'page_name': 'wb_downloads',
        })

    @http.route('/my/downloads/<int:license_id>/<int:release_id>',
                type='http', auth='user', website=True)
    def portal_download_release(self, license_id, release_id, **kw):
        license = self._resolve_license(license_id)
        if not license:
            return request.not_found()

        release = request.env['wb.product.release'].sudo().browse(release_id)
        if not release.exists():
            return request.not_found()
        if release.product_tmpl_id != license.product_id.product_tmpl_id:
            return request.not_found()
        if release.state != 'published' or not release.attachment_id:
            return request.not_found()

        request.env['wb.license.event'].sudo().log_event(
            license, 'download',
            ip_address=request.httprequest.remote_addr or '',
            user_agent=(request.httprequest.user_agent.string or '')[:255]
            if request.httprequest.user_agent else '',
            details={
                'release_id': release.id,
                'version': release.version or '',
                'is_current': release.is_current,
            },
        )
        release.increment_download_count()

        attachment = release.attachment_id
        filename = (
            release.filename
            or attachment.name
            or f"{license.product_code or 'module'}-{release.version}.tar.gz"
        )
        raw = attachment.raw or b''
        return request.make_response(
            raw,
            headers=[
                ('Content-Type', 'application/gzip'),
                ('Content-Disposition', f'attachment; filename="{filename}"'),
                ('Content-Length', str(len(raw))),
            ],
        )
