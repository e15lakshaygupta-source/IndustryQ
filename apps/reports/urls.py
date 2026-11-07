from django.urls import path
from . import views

app_name = "reports"
urlpatterns = [
    path("", views.report_index, name="index"),
    path("billing/", views.billing_reports, name="billing"),
    path("executive/", views.executive_report, name="executive"),
    path("advanced/", views.advanced_business_intelligence, name="advanced"),
    path("workforce/", views.workforce_report, name="workforce"),
    path("quality-energy/", views.quality_energy_report, name="quality_energy"),
    path("production/", views.unavailable_report, {"kind": "production"}, name="production"),
    path("inventory/", views.unavailable_report, {"kind": "inventory"}, name="inventory"),
    path("machines/", views.unavailable_report, {"kind": "machines"}, name="machines"),
    path("notifications/", views.notifications, name="notifications"),
]
