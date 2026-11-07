from django.urls import path
from apps.operations import views
from . import views as invoice_views

app_name = "billing"
urlpatterns = [
    path("", views.billing_home, name="home"),
    path("customers/add/", views.customer_create, name="customer_create"),
    path("customers/<int:pk>/", views.customer_detail, name="customer_detail"),
    path("documents/add/", invoice_views.invoice_create, name="document_create"),
    path("purchase-bills/add/", invoice_views.purchase_bill_create, name="purchase_bill_create"),
    path("purchase-bills/<int:pk>/verify/", invoice_views.purchase_bill_verify, name="purchase_bill_verify"),
    path("purchase-bills/<int:pk>/pdf/", invoice_views.purchase_bill_pdf_print, name="purchase_bill_pdf"),
    path("purchase-bills/<int:pk>/pdf/download/", invoice_views.purchase_bill_pdf_download, name="purchase_bill_pdf_download"),
    path("purchase-bills/<int:pk>/source-pdf/", invoice_views.purchase_bill_source_pdf, name="purchase_bill_source_pdf"),
    path("adjustments/", invoice_views.credit_debit_note_list, name="adjustment_list"),
    path("adjustments/add/<int:document_id>/", invoice_views.credit_debit_note_create, name="adjustment_create"),
    path("adjustments/<int:pk>/", invoice_views.credit_debit_note_detail, name="adjustment_detail"),
    path("adjustments/<int:pk>/approve/", invoice_views.credit_debit_note_approve, name="adjustment_approve"),
    path("documents/<int:pk>/", views.billing_detail, name="document_detail"),
    path("documents/<int:pk>/approve/", invoice_views.invoice_approve, name="invoice_approve"),
    path("documents/<int:pk>/issue/", invoice_views.invoice_issue, name="invoice_issue"),
    path("documents/<int:pk>/cancel/", invoice_views.invoice_cancel, name="invoice_cancel"),
    path("documents/<int:pk>/pdf/", invoice_views.invoice_pdf, name="invoice_pdf"),
    path("documents/<int:pk>/pdf/download/", invoice_views.invoice_pdf_download, name="invoice_pdf_download"),
    path("payments/<int:payment_id>/receipt/", invoice_views.payment_receipt, name="payment_receipt"),
    path("payments/<int:payment_id>/receipt/download/", invoice_views.payment_receipt_download, name="payment_receipt_download"),
    path("customers/<int:pk>/statement/", invoice_views.customer_statement, name="customer_statement"),
    path("customers/<int:pk>/statement.csv", invoice_views.customer_statement_csv, name="customer_statement_csv"),
    path("gst/", invoice_views.gst_report, name="gst_report"),
    path("gst/reconciliation.csv", invoice_views.gst_reconciliation_export, name="gst_reconciliation_export"),
]
