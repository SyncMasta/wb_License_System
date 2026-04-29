import logging

from . import models
from . import wizards
from . import controllers

_logger = logging.getLogger(__name__)


def _post_init_auto_lookup(env):
    """Probiert Zero-Touch-Aktivierung fuer alle bereits installierten
    WB-Produkt-Module beim Install/Update von wb_license_client.

    Best-effort — wenn der Lizenz-Server nicht erreichbar ist oder kein
    Match gefunden wird, faellt der Anwender stillschweigend auf den
    manuellen Activate-Wizard zurueck. Niemals ein Module-Install
    blockieren.
    """
    try:
        env['wb.license.client'].auto_lookup_all_installed()
    except Exception as e:
        _logger.warning(
            "[wb_license_client] post_init Auto-Lookup gescheitert: %s", e)
