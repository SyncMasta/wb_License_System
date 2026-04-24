{
    'name': 'WB Subscription & License Platform',
    'version': '19.0.1.0.0',
    'category': 'Sales/Subscriptions',
    'summary': 'Vertriebs-Backend für WISSEN BERATUNG Produkt-Module',
    'description': """
WB Subscription & License Platform
==================================

Produkt-agnostisches Vertriebs-Backend für WISSEN BERATUNG Odoo-Module.

Funktionen:
- Lizenzschlüssel-Verwaltung (Public Key + bcrypt-gehashter Activation-Code)
- Odoo-Instanz-Bindung (Domain + DB-UUID)
- Portal-Ticket-Flow mit Email-OTP für Code-Auslieferung
- Trial-System (7 Tage, Lead-Capture)
- Migration-Workflow (Domain-Umzug mit Approval)
- Integration mit sale.subscription, product.product, account_followup
- Audit-Log aller License-Events
- Admin-Dashboard mit MRR/ARR

Siehe ARCHITECTURE.md v1.5 für Details.
    """,
    'author': 'WISSEN BERATUNG (Tobias Wissen)',
    'website': 'https://www.wissen-beratung.de',
    'license': 'OPL-1',
    'depends': [
        'base',
        'mail',
        'product',
        'sale_subscription',
        'account',
        'account_followup',
        'portal',
    ],
    'external_dependencies': {
        'python': ['bcrypt', 'cryptography'],
    },
    'data': [
        'security/wb_subscription_security.xml',
        'security/ir.model.access.csv',
        'data/ir_sequence_data.xml',
        'data/wb_license_tag_data.xml',
        'views/wb_license_key_views.xml',
        'views/wb_license_event_views.xml',
        'views/wb_license_tag_views.xml',
        'views/wb_activation_ticket_views.xml',
        'views/wb_license_migration_views.xml',
        'views/wb_license_trial_views.xml',
        'views/wb_notification_log_views.xml',
        'views/wb_rate_limit_entry_views.xml',
        'views/product_template_views.xml',
        'views/sale_subscription_views.xml',
        'views/res_partner_views.xml',
        'views/menu.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
    'post_init_hook': '_wb_subscription_post_init',
}
