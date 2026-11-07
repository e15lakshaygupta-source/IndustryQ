from django.urls import path

from . import views

app_name = "crm"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("leads/add/", views.lead_create, name="lead_create"),
    path("leads/<int:pk>/stage/", views.lead_stage_update, name="lead_stage_update"),
    path("enquiries/add/", views.enquiry_create, name="enquiry_create"),
    path("enquiries/<int:pk>/status/", views.enquiry_status_update, name="enquiry_status_update"),
    path("quotations/add/", views.quotation_create, name="quotation_create"),
    path("quotations/<int:pk>/status/", views.quotation_status_update, name="quotation_status_update"),
    path("follow-ups/add/", views.follow_up_create, name="follow_up_create"),
    path("follow-ups/<int:pk>/complete/", views.follow_up_complete, name="follow_up_complete"),
]
