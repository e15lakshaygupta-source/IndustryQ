from django.urls import path
from . import views

app_name = "core"
urlpatterns = [path("", views.executive_dashboard, name="executive_dashboard")]
