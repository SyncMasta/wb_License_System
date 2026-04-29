{
    'name': 'WB License Client',
    'version': '19.0.2.1.0',
    'category': 'Technical',
    'summary': 'Lizenz-Client für WISSEN BERATUNG Produkt-Module',
    'description': """
WB License Client
==================

Gemeinsames Basis-Modul für alle WISSEN BERATUNG Produkt-Module.
Läuft beim Kunden und kümmert sich um:

* Lizenz-Aktivierung (UI-Wizard zum Key+Code eingeben)
* Täglicher Ping an den WB-Lizenz-Server
* Status-Caching (offline-tolerant, 30 Tage)
* UI-Banner (Ablauf, Grace, Expired)
* Feature-Gates (Decorator @license_required)
* Migration-Request-UI

Produkt-agnostisch: Jedes WB-Produkt-Modul hängt sich ein über
``depends = ['wb_license_client']`` und nutzt:

    env['wb.license.client'].check_license('TELE')
    @license_required('TELE')
    @license_required('TELE', min_cache_age_days=7)   # strenger bei externen Kosten

Siehe ARCHITECTURE.md v1.5 und docs/modules/wb_license_client.md für Details.
    """,
    'author': 'WISSEN BERATUNG (Tobias Wissen)',
    'website': 'https://www.wissen-beratung.de',
    'license': 'OPL-1',
    'depends': [
        'base',
        'mail',
        'web',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_config_parameter_data.xml',
        'data/ir_cron_data.xml',
        'data/mail_template_data.xml',
        'wizards/wb_license_activate_wizard_views.xml',
        'wizards/wb_license_migrate_wizard_views.xml',
        'views/wb_license_info_views.xml',
        'views/res_config_settings_views.xml',
        'views/menu.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'wb_license_client/static/src/js/license_banner.js',
            'wb_license_client/static/src/scss/license_banner.scss',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
    'post_init_hook': '_post_init_auto_lookup',
}
