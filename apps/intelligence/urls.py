from django.urls import path
from . import views

app_name = "intelligence"
urlpatterns = [
    path("", views.intelligence_home, name="home"),
    path("quality/submit/", views.inspection_create, name="inspection_create"),
    path("quality/history/", views.inspection_history, name="inspection_history"),
    path("quality/", views.inspection_history, name="quality_home"),
    path("quality/<int:pk>/review/", views.inspection_review, name="inspection_review"),
    path("energy/add/", views.energy_reading_create, name="energy_create"),
    path("sustainability/", views.sustainability_dashboard, name="sustainability"),
    path("sustainability/add/", views.sustainability_reading_create, name="sustainability_add"),
    path("machines/", views.machine_home, name="machines"),
    path("machines/predictive/", views.predictive_maintenance, name="predictive_maintenance"),
    path("machines/register/", views.machine_create, name="machine_create"),
    path("machines/<int:pk>/edit/", views.machine_edit, name="machine_edit"),
    path("machines/maintenance/add/", views.maintenance_create, name="maintenance_create"),
    path("safety/incidents/", views.safety_incidents, name="safety_incidents"),
    path("assistant/", views.assistant_view, name="assistant"),
    path("automation/", views.workflow_rules, name="workflow_rules"),
    path("automation/run/", views.workflow_run, name="workflow_run"),
    path("automation/rules/<int:pk>/run/", views.workflow_run, name="workflow_run_rule"),
    path("automation/rules/<int:pk>/toggle/", views.workflow_toggle, name="workflow_toggle"),
]
