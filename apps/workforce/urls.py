from django.urls import path
from . import views

app_name = "workforce"
urlpatterns = [
    path("", views.workforce_home, name="home"),
    path("employees/", views.employee_directory, name="employee_list"),
    path("employees/add/", views.employee_create, name="employee_create"),
    path("employees/<int:pk>/", views.employee_detail, name="employee_detail"),
    path("employees/<int:pk>/edit/", views.employee_edit, name="employee_edit"),
    path("employees/<int:pk>/performance/", views.performance_create, name="performance_create"),
    path("recruitment/", views.recruitment, name="recruitment"),
    path("recruitment/<int:pk>/stage/", views.applicant_status, name="applicant_status"),
    path("payroll/", views.payroll, name="payroll"),
    path("training/", views.training, name="training"),
    path("training/<int:pk>/complete/", views.training_complete, name="training_complete"),
    path("shifts/", views.shift_list, name="shift_list"),
    path("shifts/add/", views.shift_create, name="shift_create"),
    path("shifts/<int:pk>/assign/", views.shift_assign, name="shift_assign"),
    path("attendance/", views.attendance_list, name="attendance_list"),
    path("attendance/add/", views.attendance_create, name="attendance_create"),
    path("attendance/punch/", views.attendance_punch, name="attendance_punch"),
    path("attendance/report/", views.attendance_report, name="attendance_report"),
    path("leave/", views.leave_list, name="leave_list"),
    path("leave/<int:pk>/review/", views.leave_review, name="leave_review"),
    path("portal/", views.employee_portal, name="portal"),
]
