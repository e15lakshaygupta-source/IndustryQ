from django.urls import path
from . import views

app_name = "enterprise"
urlpatterns = [
    path("", views.enterprise_home, name="home"),
    path("documents/", views.documents, name="documents"),
    path("documents/<int:pk>/download/", views.document_download, name="document_download"),
    path("compliance/", views.compliance, name="compliance"),
    path("compliance/<int:pk>/status/", views.compliance_status, name="compliance_status"),
    path("projects/", views.projects, name="projects"),
    path("projects/tasks/<int:pk>/status/", views.project_task_status, name="project_task_status"),
    path("portal/", views.external_portal, name="portal"),
    path("portal/access/", views.portal_admin, name="portal_admin"),
    path("mobile/", views.mobile_workforce, name="mobile"),
    path("integrations/", views.integrations, name="integrations"),
    path("integrations/<int:pk>/toggle/", views.integration_toggle, name="integration_toggle"),
    path("api/v1/<uuid:public_id>/summary/", views.integration_summary_api, name="integration_summary_api"),
    path("recovery/", views.recovery_center, name="recovery"),
    path("security/", views.security_center, name="security"),
    path("service-worker.js", views.service_worker, name="service_worker"),
]
