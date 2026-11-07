from apps.operations.models import BillingDocument


class BillingDocumentPermission(BillingDocument):
    class Meta:
        proxy = True
        app_label = "billing"
        default_permissions = ()
        permissions = [("manage_billing", "Can manage billing documents, customers, and payments")]
