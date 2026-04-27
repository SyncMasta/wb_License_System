import logging

from . import models
from . import controllers

_logger = logging.getLogger(__name__)


FERNET_CONFIG_PARAM = 'wb_subscription.fernet_key'


def _wb_subscription_post_init(env):
    """Läuft einmalig beim Install — generiert Fernet-Key wenn nicht vorhanden.

    Idempotent: Wird der Key später von Hand durch einen ENV-Wert oder manuell
    im Parameter überschrieben, wird beim Re-Install NICHT neu generiert.
    Wichtig, weil ein Key-Wechsel alle Pending-Tickets entwertet.

    Reihenfolge:
    1. Wenn ENV-Variable WB_SUBSCRIPTION_FERNET_KEY gesetzt ist → nichts tun,
       der Key-Generator bevorzugt sowieso die ENV-Variable.
    2. Wenn ir.config_parameter bereits einen Key enthält → nichts tun.
    3. Sonst: neuen Key generieren und in ir.config_parameter speichern.

    Läuft NICHT bei Updates (nur Install). Der Key bleibt also über
    Re-Installation und Updates stabil.
    """
    import os
    from cryptography.fernet import Fernet

    if os.environ.get('WB_SUBSCRIPTION_FERNET_KEY'):
        _logger.info(
            "[wb_subscription] Fernet-Key aus ENV-Variable WB_SUBSCRIPTION_FERNET_KEY "
            "erkannt — kein Auto-Generate in ir.config_parameter."
        )
        return

    icp = env['ir.config_parameter'].sudo()
    existing = icp.get_param(FERNET_CONFIG_PARAM)
    if existing:
        _logger.info(
            "[wb_subscription] Fernet-Key bereits in ir.config_parameter vorhanden — "
            "kein Auto-Generate (idempotent)."
        )
        return

    new_key = Fernet.generate_key().decode('utf-8')
    icp.set_param(FERNET_CONFIG_PARAM, new_key)
    _logger.warning(
        "[wb_subscription] Fernet-Key wurde automatisch generiert und in "
        "ir.config_parameter '%s' abgelegt. Für höhere Sicherheit: In ENV-Variable "
        "WB_SUBSCRIPTION_FERNET_KEY umziehen und Parameter löschen.",
        FERNET_CONFIG_PARAM,
    )
