import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.shortcuts import render
from django.utils import timezone

from apps.accounts.models import UserActivity, UserProfile
from apps.organization.models import Company, Factory, Department
from apps.workforce.models import Employee, Attendance, LeaveRequest
from apps.operations.models import (
    BillingDocument,
    BillingPayment,
    CreditDebitNote,
    ProductionEntry,
    ProductionPlan,
    WorkOrder,
)
from apps.operations.views import billing_manager
from apps.operations.models import InventoryItem, StockMovement
from apps.intelligence.models import Machine
from apps.enterprise.models import IndustrialProject, ProjectTask


def _billing_net(total, adjustments):
    return max(total - adjustments, Decimal("0.00"))


def _chart_rows(values, limit=8):
    return [
        {"label": label, "value": float(value)}
        for label, value in sorted(values.items(), key=lambda item: item[1], reverse=True)[:limit]
    ]


def _billing_dashboard_context(request, profile, factories):
    if not billing_manager(request.user, profile):
        return {"can_view_billing": False}

    today = timezone.localdate()
    try:
        selected_month = datetime.strptime(request.GET.get("month", ""), "%Y-%m").date().replace(day=1)
    except ValueError:
        selected_month = today.replace(day=1)
    current_month_index = today.year * 12 + today.month - 1
    month_starts = []
    for offset in range(11, -1, -1):
        year, month_index = divmod(current_month_index - offset, 12)
        month_starts.append(date(year, month_index + 1, 1))

    scoped_docs = BillingDocument.objects.filter(factory__in=factories)
    currencies = list(
        scoped_docs.order_by("currency_code").values_list("currency_code", flat=True).distinct()
    )
    currency = request.GET.get("currency", "").upper()
    if currency not in currencies:
        currency = "INR" if "INR" in currencies else (currencies[0] if currencies else "INR")

    sales_docs = list(
        scoped_docs.filter(
            kind=BillingDocument.Kind.SALES_INVOICE,
            currency_code=currency,
            status__in=[
                BillingDocument.Status.ISSUED,
                BillingDocument.Status.PART_PAID,
                BillingDocument.Status.PAID,
            ],
        ).select_related("customer").prefetch_related("lines__product", "payments")
    )
    sales_ids = [invoice.pk for invoice in sales_docs]
    notes = list(
        CreditDebitNote.objects.filter(
            source_document_id__in=sales_ids,
            kind=CreditDebitNote.Kind.CREDIT_NOTE,
            status=CreditDebitNote.Status.APPROVED,
        ).select_related("source_document__customer").prefetch_related("lines__source_line")
    )
    adjustment_by_invoice = defaultdict(Decimal)
    adjustment_by_customer_month = defaultdict(Decimal)
    adjustment_by_product_line = defaultdict(Decimal)
    gst_adjustments = defaultdict(Decimal)
    for note in notes:
        adjustment_by_invoice[note.source_document_id] += note.total
        adjustment_by_customer_month[
            (note.source_document.customer_id, note.issue_date.year, note.issue_date.month)
        ] += note.total
        for line in note.lines.all():
            adjustment_by_product_line[line.source_line_id] += line.total
            gst_adjustments["cgst"] += line.cgst_amount
            gst_adjustments["sgst"] += line.sgst_amount
            gst_adjustments["igst"] += line.igst_amount

    total_sales = sum(
        (_billing_net(invoice.total, adjustment_by_invoice[invoice.pk]) for invoice in sales_docs),
        Decimal("0.00"),
    )
    monthly_sales = sum(
        (
            invoice.total
            for invoice in sales_docs
            if invoice.issue_date.year == selected_month.year
            and invoice.issue_date.month == selected_month.month
        ),
        Decimal("0.00"),
    ) - sum(
        (
            note.total for note in notes
            if note.issue_date.year == selected_month.year
            and note.issue_date.month == selected_month.month
        ),
        Decimal("0.00"),
    )
    amount_received = BillingPayment.objects.filter(
        document_id__in=sales_ids,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    gst_invoices = {
        "cgst": sum((invoice.cgst_amount for invoice in sales_docs), Decimal("0.00")),
        "sgst": sum((invoice.sgst_amount for invoice in sales_docs), Decimal("0.00")),
        "igst": sum((invoice.igst_amount for invoice in sales_docs), Decimal("0.00")),
    }
    gst_breakdown = {
        key: max(gst_invoices[key] - gst_adjustments[key], Decimal("0.00"))
        for key in ("cgst", "sgst", "igst")
    }

    open_receivables = [
        invoice for invoice in sales_docs
        if invoice.status in (BillingDocument.Status.ISSUED, BillingDocument.Status.PART_PAID)
    ]
    customer_sales = defaultdict(Decimal)
    receivable_by_customer = defaultdict(Decimal)
    for invoice in sales_docs:
        customer_name = invoice.customer.name if invoice.customer_id else "Unassigned customer"
        customer_sales[customer_name] += _billing_net(
            invoice.total, adjustment_by_invoice[invoice.pk]
        )
        if invoice.status in (BillingDocument.Status.ISSUED, BillingDocument.Status.PART_PAID):
            receivable_by_customer[customer_name] += max(
                invoice.total - invoice.amount_paid - adjustment_by_invoice[invoice.pk],
                Decimal("0.00"),
            )

    payable_docs = list(
        scoped_docs.filter(
            kind=BillingDocument.Kind.SUPPLIER_BILL,
            currency_code=currency,
            status__in=[
                BillingDocument.Status.APPROVED,
                BillingDocument.Status.ISSUED,
                BillingDocument.Status.PART_PAID,
            ],
        )
    )
    debit_adjustments = defaultdict(Decimal)
    for note in CreditDebitNote.objects.filter(
        source_document_id__in=[bill.pk for bill in payable_docs],
        kind=CreditDebitNote.Kind.DEBIT_NOTE,
        status=CreditDebitNote.Status.APPROVED,
    ):
        debit_adjustments[note.source_document_id] += note.total
    supplier_payables = sum(
        (
            max(bill.total - bill.amount_paid - debit_adjustments[bill.pk], Decimal("0.00"))
            for bill in payable_docs
        ),
        Decimal("0.00"),
    )
    overdue_count = sum(
        invoice.due_date is not None
        and invoice.due_date < today
        and invoice.total - invoice.amount_paid - adjustment_by_invoice[invoice.pk] > 0
        for invoice in open_receivables
    )

    sales_by_month = defaultdict(Decimal)
    collections_by_month = defaultdict(Decimal)
    supplier_payments_by_month = defaultdict(Decimal)
    months = {(month.year, month.month) for month in month_starts}
    for invoice in sales_docs:
        key = (invoice.issue_date.year, invoice.issue_date.month)
        if key in months:
            sales_by_month[key] += invoice.total
    for note in notes:
        key = (note.issue_date.year, note.issue_date.month)
        if key in months:
            sales_by_month[key] -= note.total
    for payment in BillingPayment.objects.filter(
        document_id__in=sales_ids,
        payment_date__gte=month_starts[0],
        payment_date__lte=today,
    ).values("payment_date", "amount"):
        key = (payment["payment_date"].year, payment["payment_date"].month)
        collections_by_month[key] += payment["amount"]
    for payment in BillingPayment.objects.filter(
        document__factory__in=factories,
        document__kind=BillingDocument.Kind.SUPPLIER_BILL,
        document__currency_code=currency,
        payment_date__gte=month_starts[0],
        payment_date__lte=today,
    ).values("payment_date", "amount"):
        key = (payment["payment_date"].year, payment["payment_date"].month)
        supplier_payments_by_month[key] += payment["amount"]

    product_sales = defaultdict(Decimal)
    for invoice in sales_docs:
        for line in invoice.lines.all():
            label = line.product.name if line.product_id else line.description
            product_sales[label] += max(
                line.gross_total - adjustment_by_product_line[line.pk],
                Decimal("0.00"),
            )
    charts = {
        "monthly_sales": [
            {"label": month.strftime("%b %y"), "value": float(max(sales_by_month[(month.year, month.month)], Decimal("0.00")))}
            for month in month_starts
        ],
        "payment_collection": [
            {"label": month.strftime("%b %y"), "value": float(collections_by_month[(month.year, month.month)])}
            for month in month_starts
        ],
        "customer_sales": _chart_rows(customer_sales),
        "product_sales": _chart_rows(product_sales),
        "gst_breakdown": [
            {"label": name, "value": float(gst_breakdown[key])}
            for key, name in (("cgst", "CGST"), ("sgst", "SGST"), ("igst", "IGST"))
        ],
        "receivables": _chart_rows(receivable_by_customer),
        "supplier_payments": [
            {"label": month.strftime("%b %y"), "value": float(supplier_payments_by_month[(month.year, month.month)])}
            for month in month_starts
        ],
    }
    outstanding_receivables = sum(
        (
            max(invoice.total - invoice.amount_paid - adjustment_by_invoice[invoice.pk], Decimal("0.00"))
            for invoice in open_receivables
        ),
        Decimal("0.00"),
    )
    return {
        "can_view_billing": True,
        "billing_currency": currency,
        "billing_currencies": currencies,
        "billing_month": selected_month.strftime("%Y-%m"),
        "billing_month_options": [month.strftime("%Y-%m") for month in month_starts],
        "billing_metrics": {
            "total_sales": total_sales,
            "monthly_revenue": monthly_sales,
            "amount_received": amount_received,
            "outstanding_receivables": outstanding_receivables,
            "supplier_payables": supplier_payables,
            "total_gst": sum(gst_breakdown.values(), Decimal("0.00")),
            "total_invoices": len(sales_docs),
            "overdue_invoices": overdue_count,
        },
        "billing_charts": charts,
    }


@login_required
def executive_dashboard(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user, defaults={"role": UserProfile.Role.SUPER_ADMIN if request.user.is_superuser else UserProfile.Role.EMPLOYEE})
    companies, factories, departments = Company.objects.all(), Factory.objects.all(), Department.objects.all()
    employees = Employee.objects.all()
    attendance, leaves = Attendance.objects.all(), LeaveRequest.objects.all()
    if profile.role == UserProfile.Role.COMPANY_ADMIN:
        companies = companies.filter(pk=profile.company_id) if profile.company_id else companies.none()
        factories = factories.filter(company__in=companies)
        departments = departments.filter(factory__in=factories)
        employees = employees.filter(factory__in=factories)
        attendance = attendance.filter(employee__factory__in=factories)
        leaves = leaves.filter(employee__factory__in=factories)
    elif profile.role in (UserProfile.Role.FACTORY_MANAGER, UserProfile.Role.DEPARTMENT_MANAGER, UserProfile.Role.EMPLOYEE):
        factories = factories.filter(pk=profile.factory_id)
        departments = departments.filter(factory_id=profile.factory_id)
        employees = employees.filter(factory_id=profile.factory_id)
        attendance = attendance.filter(employee__factory_id=profile.factory_id)
        leaves = leaves.filter(employee__factory_id=profile.factory_id)
        companies = companies.filter(factories__in=factories).distinct()
        if profile.role in (UserProfile.Role.DEPARTMENT_MANAGER, UserProfile.Role.EMPLOYEE):
            departments = departments.filter(pk=profile.department_id)
            employees = employees.filter(department_id=profile.department_id)
            attendance = attendance.filter(employee__department_id=profile.department_id)
            leaves = leaves.filter(employee__department_id=profile.department_id)

    production_entries = ProductionEntry.objects.filter(factory__in=factories)
    production_plans = ProductionPlan.objects.filter(factory__in=factories)
    work_orders = WorkOrder.objects.filter(factory__in=factories)
    inventory_items = InventoryItem.objects.filter(factory__in=factories, is_active=True)
    machines = Machine.objects.filter(factory__in=factories)
    projects = IndustrialProject.objects.filter(factory__in=factories)
    if profile.role in (UserProfile.Role.DEPARTMENT_MANAGER, UserProfile.Role.EMPLOYEE):
        production_entries = production_entries.filter(department_id=profile.department_id)
        production_plans = production_plans.filter(department_id=profile.department_id)
        work_orders = work_orders.filter(department_id=profile.department_id)
    today = timezone.localdate()
    present_statuses = [Attendance.Status.PRESENT, Attendance.Status.LATE, Attendance.Status.HALF_DAY]
    production_today = production_entries.filter(production_date=today)
    actual_today = production_today.aggregate(total=Sum("actual_quantity"))["total"] or 0
    rejected_today = production_today.aggregate(total=Sum("rejected_quantity"))["total"] or 0
    planned_today = work_orders.filter(scheduled_start__date=today).aggregate(total=Sum("planned_quantity"))["total"] or 0
    present_today = attendance.filter(date=today, status__in=present_statuses).count()
    absent_today = attendance.filter(date=today, status=Attendance.Status.ABSENT).count()
    pending_leaves = leaves.filter(status=LeaveRequest.ApprovalStatus.PENDING).count()
    factory_rows = list(factories.annotate(
        employee_total=Count("employees", filter=Q(employees__employment_status=Employee.EmploymentStatus.ACTIVE), distinct=True),
        departments_total=Count("departments", distinct=True),
        present_total=Count("employees__attendance_records", filter=Q(employees__attendance_records__date=today, employees__attendance_records__status__in=present_statuses), distinct=True),
        absent_total=Count("employees__attendance_records", filter=Q(employees__attendance_records__date=today, employees__attendance_records__status=Attendance.Status.ABSENT), distinct=True),
    ).order_by("name"))

    last_7_days = [today - timedelta(days=i) for i in reversed(range(7))]
    attendance_chart = [{"day": day.strftime("%a"), "present": attendance.filter(date=day, status__in=present_statuses).count(), "absent": attendance.filter(date=day, status=Attendance.Status.ABSENT).count()} for day in last_7_days]
    production_chart = []
    for day in last_7_days:
        totals = production_entries.filter(production_date=day).aggregate(actual=Sum("actual_quantity"), rejected=Sum("rejected_quantity"))
        production_chart.append({"day": day.strftime("%a"), "actual": totals["actual"] or 0, "rejected": totals["rejected"] or 0})
    alerts = []
    if pending_leaves: alerts.append({"level": "warning", "title": "Pending leave requests", "message": f"{pending_leaves} leave request(s) need review."})
    if absent_today: alerts.append({"level": "danger", "title": "Absence recorded today", "message": f"{absent_today} employee absence(s) recorded."})
    if not attendance.filter(date=today).exists(): alerts.append({"level": "info", "title": "Attendance data missing", "message": "No attendance records have been entered for today."})
    recent_activities = UserActivity.objects.select_related("user").order_by("-created_at")[:10]
    context = {
        "profile": profile, "company_count": companies.count(), "factory_count": factories.count(),
        "department_count": departments.count(), "employee_count": employees.filter(employment_status=Employee.EmploymentStatus.ACTIVE).count(),
        "present_today": present_today, "absent_today": absent_today, "pending_leaves": pending_leaves,
        "attendance_rate": round(present_today * 100 / (present_today + absent_today), 1) if present_today + absent_today else None,
        "factory_rows": factory_rows, "attendance_chart_json": json.dumps(attendance_chart),
        "production_chart": production_chart,
        "alerts": alerts, "recent_activities": recent_activities,
        "operations_configured": True,
        "production_actual_today": actual_today,
        "production_rejected_today": rejected_today,
        "production_planned_today": planned_today,
          "asset_count": machines.count(),
          "ongoing_operations": work_orders.exclude(status__in=[WorkOrder.Status.COMPLETED, WorkOrder.Status.CANCELLED]).count(),
          "project_count": projects.exclude(status=IndustrialProject.Status.COMPLETE).count(),
          "inventory_module_count": inventory_items.count(),
        "module_distribution": [
            {"label": "Operations", "value": production_entries.count()},
            {"label": "Assets", "value": machines.count()},
            {"label": "Workforce", "value": employees.filter(employment_status=Employee.EmploymentStatus.ACTIVE).count()},
            {"label": "Intelligence", "value": work_orders.count() + projects.count()},
          ],
        "open_work_orders": work_orders.exclude(status__in=[WorkOrder.Status.COMPLETED, WorkOrder.Status.CANCELLED]).count(),
        "active_production_plans": production_plans.exclude(status__in=[ProductionPlan.Status.COMPLETED, ProductionPlan.Status.CANCELLED]).count(),
    }
    context.update(_billing_dashboard_context(request, profile, factories))
    return render(request, "core/executive_dashboard.html", context)

