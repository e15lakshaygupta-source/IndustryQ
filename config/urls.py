from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("apps.accounts.urls")),
    path("organization/", include("apps.organization.urls")),
    path("executive/", include("apps.core.urls")),
    path("intelligence/", include("apps.intelligence.urls")),
    path("workforce/", include("apps.workforce.urls")),
    path("reports/", include("apps.reports.urls")),
    path("operations/", include("apps.operations.urls")),
    path("billing/", include("apps.billing.urls")),
    path("crm/", include("apps.crm.urls")),
    path("enterprise/", include("apps.enterprise.urls")),
]
