from django.urls import path
from . import views

app_name = "organization"
urlpatterns = [
    path("", views.organization_overview, name="overview"),
    path("companies/", views.object_list, {"kind": "company"}, name="company_list"),
    path("companies/add/", views.object_form, {"kind": "company"}, name="company_create"),
    path("companies/<int:pk>/edit/", views.object_form, {"kind": "company"}, name="company_edit"),
    path("companies/<int:pk>/delete/", views.object_delete, {"kind": "company"}, name="company_delete"),
    path("factories/", views.object_list, {"kind": "factory"}, name="factory_list"),
    path("factories/add/", views.object_form, {"kind": "factory"}, name="factory_create"),
    path("factories/<int:pk>/edit/", views.object_form, {"kind": "factory"}, name="factory_edit"),
    path("factories/<int:pk>/delete/", views.object_delete, {"kind": "factory"}, name="factory_delete"),
    path("departments/", views.object_list, {"kind": "department"}, name="department_list"),
    path("departments/add/", views.object_form, {"kind": "department"}, name="department_create"),
    path("departments/<int:pk>/edit/", views.object_form, {"kind": "department"}, name="department_edit"),
    path("departments/<int:pk>/delete/", views.object_delete, {"kind": "department"}, name="department_delete"),
    path("units/", views.object_list, {"kind": "unit"}, name="unit_list"),
    path("units/add/", views.object_form, {"kind": "unit"}, name="unit_create"),
    path("units/<int:pk>/edit/", views.object_form, {"kind": "unit"}, name="unit_edit"),
    path("units/<int:pk>/delete/", views.object_delete, {"kind": "unit"}, name="unit_delete"),
]
