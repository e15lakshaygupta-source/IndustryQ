import csv
import html
import json
import re
from collections import defaultdict
from decimal import Decimal
from io import BytesIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, Spacer, Table

from apps.accounts.models import Notification, UserActivity, UserProfile
from apps.organization.models import Company, Factory, Department
from apps.workforce.models import Attendance, Employee, LeaveRequest, Shift
from apps.intelligence.models import EnergyReading, Machine, QualityInspection
from apps.operations.models import (
    BillingDocument,
    BillingPayment,
    BillingRefund,
    CreditDebitNote,
    ProductionEntry,
    ProductionPlan,
    InventoryItem,
    StockMovement,
    WorkOrder,
)
from apps.operations.views import billing_manager, inventory_balance
from apps.core.pdf_utils import (
    PALE,
    premium_document,
    premium_page_chrome,
    premium_pdf_styles,
    premium_table_style,
)
from .forms import ReportFilterForm, SavedKPIDashboardForm
from .models import SavedKPIDashboard


def report_scope(user):
    profile, _ = UserProfile.objects.get_or_create(user=user, defaults={"role": UserProfile.Role.SUPER_ADMIN if user.is_superuser else UserProfile.Role.EMPLOYEE})
    if user.is_superuser and profile.role != UserProfile.Role.SUPER_ADMIN:
        profile.role = UserProfile.Role.SUPER_ADMIN
        profile.save(update_fields=["role", "updated_at"])
    companies, factories = Company.objects.all(), Factory.objects.all().select_related("company")
    departments, employees = Department.objects.all(), Employee.objects.select_related("factory", "department")
    attendance, leaves = Attendance.objects.select_related("employee", "shift"), LeaveRequest.objects.select_related("employee")
    quality, energy = QualityInspection.objects.select_related("factory", "department"), EnergyReading.objects.select_related("factory", "department")
    if profile.role == UserProfile.Role.COMPANY_ADMIN:
        companies = companies.filter(pk=profile.company_id) if profile.company_id else companies.none()
        factories = factories.filter(company__in=companies)
        departments = departments.filter(factory__in=factories)
        employees = employees.filter(factory__in=factories)
        attendance, leaves = attendance.filter(employee__factory__in=factories), leaves.filter(employee__factory__in=factories)
        quality, energy = quality.filter(factory__in=factories), energy.filter(factory__in=factories)
    elif profile.role in (UserProfile.Role.FACTORY_MANAGER, UserProfile.Role.DEPARTMENT_MANAGER, UserProfile.Role.EMPLOYEE):
        factories = factories.filter(pk=profile.factory_id)
        departments = departments.filter(factory_id=profile.factory_id)
        employees = employees.filter(factory_id=profile.factory_id)
        attendance, leaves = attendance.filter(employee__factory_id=profile.factory_id), leaves.filter(employee__factory_id=profile.factory_id)
        quality, energy = quality.filter(factory__in=factories), energy.filter(factory__in=factories)
        companies = companies.filter(factories__in=factories).distinct()
        if profile.role in (UserProfile.Role.DEPARTMENT_MANAGER, UserProfile.Role.EMPLOYEE):
            departments = departments.filter(pk=profile.department_id)
            employees = employees.filter(department_id=profile.department_id)
            attendance, leaves = attendance.filter(employee__department_id=profile.department_id), leaves.filter(employee__department_id=profile.department_id)
            quality, energy = quality.filter(department__in=departments), energy.filter(department__in=departments)
        if profile.role == UserProfile.Role.EMPLOYEE:
            employees = employees.filter(user=user)
            attendance, leaves = attendance.filter(employee__user=user), leaves.filter(employee__user=user)
            quality, energy = quality.none(), energy.none()
    return profile, companies, factories, departments, employees, attendance, leaves, quality, energy


def date_filters(request, form, queryset, field="date"):
    if form.is_valid():
        if form.cleaned_data.get("start"): queryset = queryset.filter(**{f"{field}__gte": form.cleaned_data["start"]})
        if form.cleaned_data.get("end"): queryset = queryset.filter(**{f"{field}__lte": form.cleaned_data["end"]})
    return queryset


def csv_response(filename, headers, rows):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("\ufeff")
    def safe_cell(value):
        if isinstance(value, str) and value.lstrip(" \t\r\n")[:1] in ("=", "+", "-", "@"):
            return "'" + value
        return value

    writer = csv.writer(response)
    writer.writerow([safe_cell(value) for value in headers])
    writer.writerows([[safe_cell(value) for value in row] for row in rows])
    return response


def pdf_response(request, title, headers, rows):
    row_count = len(rows)
    styles = premium_pdf_styles()
    styles["report_title"] = ParagraphStyle(
        "PremiumReportTitle", parent=styles["title"], fontSize=19, leading=23,
    )
    styles["report_meta"] = ParagraphStyle(
        "PremiumReportMeta", parent=styles["small"], fontSize=7.5, leading=10,
    )
    styles["column_header"] = ParagraphStyle(
        "PremiumReportColumnHeader", parent=styles["section"],
        fontSize=7 if len(headers) > 7 else 7.5, leading=9,
        alignment=TA_LEFT,
    )
    styles["cell"] = ParagraphStyle(
        "PremiumReportCell", parent=styles["body"],
        fontSize=6.5 if len(headers) > 7 else 7.2, leading=9,
    )

    safe_title = html.escape(title)
    generated_at = timezone.localtime().strftime("%d %b %Y, %I:%M %p %Z")
    filename = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "report"
    buffer = BytesIO()
    document = premium_document(
        buffer,
        title=title,
        landscape_page=len(headers) > 4,
    )
    story = [
        Paragraph(safe_title, styles["report_title"]),
        Spacer(1, 1.5 * mm),
        Paragraph(
            f"Prepared for your authorized workspace &nbsp; | &nbsp; Generated {generated_at}",
            styles["report_meta"],
        ),
        Spacer(1, 4 * mm),
    ]
    summary = Table(
        [[
            Paragraph(f"<b>{row_count:,}</b>", styles["body_bold"]),
            Paragraph("records included", styles["small"]),
            Paragraph(_report_period_label(request), styles["small"]),
        ]],
        colWidths=[19 * mm, 43 * mm, document.width - 62 * mm],
    )
    summary.setStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PALE),
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#D7DEE7")),
        ("LINEBEFORE", (0, 0), (0, -1), 2.5, colors.HexColor("#D7A13D")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ])
    story.extend([summary, Spacer(1, 4 * mm)])

    table_rows = [[
        Paragraph(html.escape(str(header)), styles["column_header"])
        for header in headers
    ]]
    for row in rows:
        table_rows.append([
            Paragraph(
                html.escape(str(value if value is not None else "")).replace("\n", "<br/>"),
                styles["cell"],
            )
            for value in row
        ])
    if not rows:
        table_rows.append([
            Paragraph("No records match the selected filters.", styles["cell"])
            if index == 0 else ""
            for index in range(len(headers))
        ])

    weights = []
    for index, header in enumerate(headers):
        longest = len(str(header))
        for row in rows[:200]:
            if index < len(row):
                longest = max(longest, min(len(str(row[index] or "")), 32))
        weights.append(max(7, min(longest, 32)) ** 0.72)
    weight_total = sum(weights) or 1
    widths = [document.width * weight / weight_total for weight in weights]
    report_table = Table(
        table_rows,
        colWidths=widths,
        repeatRows=1,
        hAlign="LEFT",
        splitByRow=1,
    )
    report_table.setStyle(premium_table_style())
    report_table.setStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("SPAN", (0, 1), (-1, 1)) if not rows else ("TOPPADDING", (0, 0), (-1, 0), 5),
    ])
    story.append(report_table)

    document.build(
        story,
        onFirstPage=lambda canvas, doc: premium_page_chrome(canvas, doc, "Report"),
        onLaterPages=lambda canvas, doc: premium_page_chrome(canvas, doc, "Report"),
    )
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}.pdf"'
    return response


def _report_period_label(request):
    start = request.GET.get("start")
    end = request.GET.get("end")
    if start and end:
        return f"Period: {html.escape(start)} – {html.escape(end)}"
    if start:
        return f"From: {html.escape(start)}"
    if end:
        return f"Through: {html.escape(end)}"
    return "All dates in selected scope"


def export(request, title, headers, rows):
    fmt = request.GET.get("format")
    if fmt == "csv": return csv_response(f"{title.lower().replace(' ', '-')}.csv", headers, rows)
    if fmt == "pdf": return pdf_response(request, title, headers, rows)
    return None


@login_required
def report_index(request):
    profile, companies, factories, departments, employees, attendance, leaves, quality, energy = report_scope(request.user)
    return render(request, "reports/index.html", {
        "source_modules": {"production": False, "inventory": False, "machines": False},
        "can_view_billing": billing_manager(request.user, profile),
    })


@login_required
def executive_report(request):
    profile, companies, factories, departments, employees, attendance, leaves, quality, energy = report_scope(request.user)
    form = ReportFilterForm(request.GET)
    period_attendance = date_filters(request, form, attendance, "date")
    period_quality = date_filters(request, form, quality, "created_at__date")
    period_energy = date_filters(request, form, energy, "reading_at__date")
    today = timezone.localdate()
    statuses = [Attendance.Status.PRESENT, Attendance.Status.LATE, Attendance.Status.HALF_DAY]
    cards = {"companies": companies.count(), "factories": factories.count(), "departments": departments.count(), "employees": employees.filter(employment_status=Employee.EmploymentStatus.ACTIVE).count(), "attendance": period_attendance.filter(status__in=statuses).count(), "absent": period_attendance.filter(status=Attendance.Status.ABSENT).count(), "pending_leaves": leaves.filter(status=LeaveRequest.ApprovalStatus.PENDING).count(), "quality_failed": period_quality.filter(status=QualityInspection.Status.FAIL).count(), "energy_kwh": period_energy.aggregate(total=Sum("consumption_kwh"))["total"]}
    production_entries = ProductionEntry.objects.filter(factory__in=factories)
    production_plans = ProductionPlan.objects.filter(factory__in=factories)
    work_orders = WorkOrder.objects.filter(factory__in=factories)
    if profile.role in (UserProfile.Role.DEPARTMENT_MANAGER, UserProfile.Role.EMPLOYEE):
        production_entries = production_entries.filter(department__in=departments)
        production_plans = production_plans.filter(department__in=departments)
        work_orders = work_orders.filter(department__in=departments)
    actual_today = production_entries.filter(production_date=today).aggregate(total=Sum("actual_quantity"))["total"] or 0
    rejected_today = production_entries.filter(production_date=today).aggregate(total=Sum("rejected_quantity"))["total"] or 0
    planned_today = work_orders.filter(scheduled_start__date=today).aggregate(total=Sum("planned_quantity"))["total"] or 0
    cards.update({"production_actual_today": actual_today, "production_planned_today": planned_today, "production_rejected_today": rejected_today})
    rows = [["Organizations", cards["companies"], cards["factories"], cards["departments"], cards["employees"]], ["Workforce", cards["attendance"], cards["absent"], cards["pending_leaves"], ""], ["Production today", actual_today, planned_today, rejected_today, ""], ["Quality and energy", cards["quality_failed"], cards["energy_kwh"] or "No data", "", ""]]
    response = export(request, "Executive report", ["Area", "Metric 1", "Metric 2", "Metric 3", "Metric 4"], rows)
    if response: return response
    factory_rows = list(factories.annotate(employee_total=Count("employees", filter=Q(employees__employment_status=Employee.EmploymentStatus.ACTIVE), distinct=True), attendance_total=Count("employees__attendance_records", filter=Q(employees__attendance_records__date=today, employees__attendance_records__status__in=statuses), distinct=True), quality_failed=Count("quality_inspections", filter=Q(quality_inspections__status=QualityInspection.Status.FAIL), distinct=True)))
    return render(request, "reports/executive.html", {"form": form, "cards": cards, "factory_rows": factory_rows, "profile": profile})


@login_required
def advanced_business_intelligence(request):
    profile, companies, factories, departments, employees, attendance, leaves, quality, energy = report_scope(request.user)
    can_view_billing = billing_manager(request.user, profile)
    form = ReportFilterForm(request.GET)
    dashboards = SavedKPIDashboard.objects.filter(user=request.user).select_related("factory")
    loaded_dashboard = dashboards.filter(pk=request.GET.get("dashboard")).first() if request.GET.get("dashboard", "").isdigit() else None
    today = timezone.localdate()
    start = form.cleaned_data.get("start") if form.is_valid() else None
    end = form.cleaned_data.get("end") if form.is_valid() else None
    selected_factory = factories.filter(pk=form.cleaned_data.get("factory")).first() if form.is_valid() and form.cleaned_data.get("factory") else None
    if loaded_dashboard and loaded_dashboard.factory_id:
        selected_factory = factories.filter(pk=loaded_dashboard.factory_id).first()
    active_factories = factories.filter(pk=selected_factory.pk) if selected_factory else factories
    entries = ProductionEntry.objects.filter(factory__in=active_factories)
    orders = WorkOrder.objects.filter(factory__in=active_factories)
    documents = BillingDocument.objects.filter(factory__in=active_factories)
    inspections = quality.filter(factory__in=active_factories)
    machines = Machine.objects.filter(factory__in=active_factories)
    if profile.role in (UserProfile.Role.DEPARTMENT_MANAGER, UserProfile.Role.EMPLOYEE):
        entries = entries.filter(department__in=departments)
        orders = orders.filter(department__in=departments)
        inspections = inspections.filter(department__in=departments)
        machines = machines.filter(department__in=departments)
    if start: entries = entries.filter(production_date__gte=start); orders = orders.filter(scheduled_start__date__gte=start); documents = documents.filter(issue_date__gte=start); inspections = inspections.filter(created_at__date__gte=start)
    if end: entries = entries.filter(production_date__lte=end); orders = orders.filter(scheduled_start__date__lte=end); documents = documents.filter(issue_date__lte=end); inspections = inspections.filter(created_at__date__lte=end)
    output = entries.aggregate(actual=Sum("actual_quantity"), rejected=Sum("rejected_quantity"), downtime=Sum("downtime_minutes"))
    inventory_value = sum((inventory_balance(item) * item.standard_cost for item in InventoryItem.objects.filter(factory__in=active_factories, is_active=True)), Decimal("0.00"))
    metrics = {
        "production": {"label": "Production output", "value": output["actual"] or 0, "unit": "units"},
        "rejects": {"label": "Rejected units", "value": output["rejected"] or 0, "unit": "units"},
        "downtime": {"label": "Recorded downtime", "value": output["downtime"] or 0, "unit": "minutes"},
        "work_orders": {"label": "Open work orders", "value": orders.exclude(status__in=[WorkOrder.Status.COMPLETED, WorkOrder.Status.CANCELLED]).count(), "unit": "orders"},
        "quality_fails": {"label": "Failed inspections", "value": inspections.filter(status=QualityInspection.Status.FAIL).count(), "unit": "records"},
        "machines": {"label": "Assets needing attention", "value": machines.exclude(status=Machine.Status.OPERATIONAL).count(), "unit": "assets"},
        "inventory_value": {"label": "Current inventory value", "value": inventory_value, "unit": "INR"},
    }
    if can_view_billing:
        sales = documents.filter(kind=BillingDocument.Kind.SALES_INVOICE).exclude(status=BillingDocument.Status.VOID)
        metrics["sales"] = {"label": "Sales invoiced", "value": sales.aggregate(total=Sum("total"))["total"] or Decimal("0.00"), "unit": "INR"}
        metrics["overdue"] = {"label": "Overdue sales invoices", "value": sales.filter(due_date__lt=today).exclude(status=BillingDocument.Status.PAID).count(), "unit": "invoices"}
    save_form = SavedKPIDashboardForm(request.POST if request.method == "POST" and request.POST.get("action") == "save" else None)
    if request.method == "POST" and request.POST.get("action") == "save" and save_form.is_valid():
        requested_keys = [key for key in request.POST.getlist("kpi") if key in metrics]
        if SavedKPIDashboard.objects.filter(user=request.user, name=save_form.cleaned_data["name"]).exists():
            save_form.add_error("name", "You already have a saved dashboard with this name.")
        elif not requested_keys:
            save_form.add_error(None, "Select at least one KPI before saving this dashboard.")
        else:
            dashboard = save_form.save(commit=False)
            dashboard.user = request.user
            dashboard.metric_keys = requested_keys
            selected_factory_id = request.POST.get("factory")
            dashboard.factory = factories.filter(pk=selected_factory_id).first() if selected_factory_id and selected_factory_id.isdigit() else None
            dashboard.save()
            messages.success(request, "KPI dashboard saved.")
            return redirect(f"{request.path}?dashboard={dashboard.pk}")
    selected = (loaded_dashboard.metric_keys if loaded_dashboard else request.GET.getlist("kpi")) or ["production", "rejects", "downtime", "work_orders", "quality_fails"]
    selected = [key for key in selected if key in metrics] or ["production"]
    selected_metrics = [{"key": key, **metrics[key]} for key in selected]
    factory_rows = []
    for factory in active_factories:
        factory_entries = entries.filter(factory=factory).aggregate(actual=Sum("actual_quantity"), rejected=Sum("rejected_quantity"), downtime=Sum("downtime_minutes"))
        row_inventory_value = sum((inventory_balance(item) * item.standard_cost for item in InventoryItem.objects.filter(factory=factory, is_active=True)), Decimal("0.00"))
        factory_rows.append({"factory": factory, "actual": factory_entries["actual"] or 0, "rejected": factory_entries["rejected"] or 0, "downtime": factory_entries["downtime"] or 0, "inventory_value": row_inventory_value, "open_orders": WorkOrder.objects.filter(factory=factory).exclude(status__in=[WorkOrder.Status.COMPLETED, WorkOrder.Status.CANCELLED]).count()})
    if request.GET.get("format") == "csv":
        headers = ["Factory", "Production units", "Rejected units", "Downtime minutes", "Inventory value", "Open work orders"]
        return csv_response("advanced-business-intelligence.csv", headers, [[row["factory"].name, row["actual"], row["rejected"], row["downtime"], row["inventory_value"], row["open_orders"]] for row in factory_rows])
    return render(request, "reports/advanced.html", {"form": form, "metrics": metrics, "selected": selected, "selected_metrics": selected_metrics, "factories": factories, "selected_factory": selected_factory, "factory_rows": factory_rows, "can_view_billing": can_view_billing, "start": start, "end": end, "dashboards": dashboards, "loaded_dashboard": loaded_dashboard, "save_form": save_form})


def _report_date_range(form):
    if not form.is_valid():
        return None, None
    return form.cleaned_data.get("start"), form.cleaned_data.get("end")


def _within_report_range(value, start, end):
    return (start is None or value >= start) and (end is None or value <= end)


def _billing_ledger_rows(documents, note_kind, start, end):
    rows = []
    for document in documents:
        party = document.customer.name if document.customer_id else document.supplier.name
        if document.kind == BillingDocument.Kind.SALES_INVOICE:
            rows.append((document.issue_date, 0, document.number, party, "Invoice", document.total, Decimal("0.00"), document.currency_code))
        else:
            rows.append((document.issue_date, 0, document.number, party, "Supplier bill", Decimal("0.00"), document.total, document.currency_code))
        for payment in document.payments.all():
            if document.kind == BillingDocument.Kind.SALES_INVOICE:
                debit, credit = Decimal("0.00"), payment.amount
            else:
                debit, credit = payment.amount, Decimal("0.00")
            rows.append((payment.payment_date, 1, payment.receipt_number, party, f"Payment · {payment.get_method_display()}", debit, credit, document.currency_code))
            for refund in payment.refunds.all():
                if document.kind == BillingDocument.Kind.SALES_INVOICE:
                    debit, credit = refund.amount, Decimal("0.00")
                else:
                    debit, credit = Decimal("0.00"), refund.amount
                rows.append((refund.refund_date, 2, refund.reference or payment.receipt_number, party, f"Refund · {refund.get_method_display()}", debit, credit, document.currency_code))
        for note in document.adjustments.filter(
            kind=note_kind,
            status=CreditDebitNote.Status.APPROVED,
        ).prefetch_related("lines"):
            if document.kind == BillingDocument.Kind.SALES_INVOICE:
                debit, credit = Decimal("0.00"), note.total
                transaction = "Credit note"
            else:
                debit, credit = note.total, Decimal("0.00")
                transaction = "Debit note"
            rows.append((note.issue_date, 3, note.number, party, transaction, debit, credit, document.currency_code))

    rows.sort(key=lambda row: (row[0], row[1], row[2]))
    balances = defaultdict(Decimal)
    result = []
    for entry_date, order, reference, party, transaction, debit, credit, currency in rows:
        balance_key = (party, currency)
        balances[balance_key] += debit - credit
        if _within_report_range(entry_date, start, end):
            result.append([
                entry_date,
                reference,
                party,
                transaction,
                currency,
                debit,
                credit,
                balances[balance_key],
            ])
    return result


@login_required
def billing_reports(request):
    profile, companies, factories, departments, employees, attendance, leaves, quality, energy = report_scope(request.user)
    if not billing_manager(request.user, profile):
        return HttpResponseForbidden("You do not have permission to access billing reports.")

    report_types = {
        "sales_register": ("Sales register", "sales_register"),
        "purchase_register": ("Purchase register", "purchase_register"),
        "customer_ledger": ("Customer ledger", "customer_ledger"),
        "supplier_ledger": ("Supplier ledger", "supplier_ledger"),
        "payment_register": ("Payment register", "payment_register"),
        "gst_summary": ("GST summary", "gst_summary"),
        "outstanding": ("Outstanding invoice report", "outstanding"),
        "overdue": ("Overdue payment report", "overdue"),
        "credit_notes": ("Credit note report", "credit_notes"),
    }
    report_key = request.GET.get("report", "sales_register")
    if report_key not in report_types:
        report_key = "sales_register"
    title = report_types[report_key][0]
    form = ReportFilterForm(request.GET)
    if not form.is_valid():
        return HttpResponseBadRequest("Invalid date range.")
    start, end = _report_date_range(form)
    documents = BillingDocument.objects.filter(
        factory__in=factories,
    ).select_related(
        "factory",
        "customer",
        "supplier",
    ).prefetch_related(
        "lines",
        "payments__refunds",
        "adjustments__lines",
    )
    currency = request.GET.get("currency", "").strip().upper()
    party_filter = request.GET.get("party", "").strip()
    parties = []
    available_currencies = list(
        BillingDocument.objects.filter(factory__in=factories)
        .order_by("currency_code")
        .values_list("currency_code", flat=True)
        .distinct()
    )
    if currency and currency in available_currencies:
        documents = documents.filter(currency_code=currency)
    else:
        currency = ""

    if report_key == "sales_register":
        records = documents.filter(
            kind=BillingDocument.Kind.SALES_INVOICE,
            status__in=[BillingDocument.Status.ISSUED, BillingDocument.Status.PART_PAID, BillingDocument.Status.PAID],
        ).order_by("issue_date", "number")
        if start:
            records = records.filter(issue_date__gte=start)
        if end:
            records = records.filter(issue_date__lte=end)
        headers = ["Invoice date", "Invoice number", "Customer", "Customer GSTIN", "Currency", "Taxable value", "GST", "Total", "Credit notes", "Net total", "Received", "Balance", "Status"]
        rows = [[
            item.issue_date,
            item.number,
            item.customer.name,
            item.customer_gstin or item.customer.tax_registration_number,
            item.currency_code,
            item.taxable_value,
            item.tax_amount,
            item.total,
            item.approved_adjustment_total,
            max(item.total - item.approved_adjustment_total, Decimal("0.00")),
            item.amount_paid,
            item.balance_due,
            item.get_status_display(),
        ] for item in records]
    elif report_key == "purchase_register":
        records = documents.filter(
            kind=BillingDocument.Kind.SUPPLIER_BILL,
            status__in=[BillingDocument.Status.APPROVED, BillingDocument.Status.PART_PAID, BillingDocument.Status.PAID],
        ).order_by("issue_date", "number")
        if start:
            records = records.filter(issue_date__gte=start)
        if end:
            records = records.filter(issue_date__lte=end)
        headers = ["Invoice date", "Bill number", "Supplier invoice", "Supplier", "Supplier GSTIN", "Currency", "Taxable value", "Input GST", "Total", "Debit notes", "Net total", "Paid", "Balance", "Status"]
        rows = [[
            item.issue_date,
            item.number,
            item.supplier_invoice_number,
            item.supplier.name,
            item.supplier_gstin or item.supplier.gstin,
            item.currency_code,
            item.taxable_value,
            item.tax_amount,
            item.total,
            item.approved_adjustment_total,
            max(item.total - item.approved_adjustment_total, Decimal("0.00")),
            item.amount_paid,
            item.balance_due,
            item.get_status_display(),
        ] for item in records]
    elif report_key in ("customer_ledger", "supplier_ledger"):
        kind = BillingDocument.Kind.SALES_INVOICE if report_key == "customer_ledger" else BillingDocument.Kind.SUPPLIER_BILL
        note_kind = CreditDebitNote.Kind.CREDIT_NOTE if report_key == "customer_ledger" else CreditDebitNote.Kind.DEBIT_NOTE
        party_field = "customer" if report_key == "customer_ledger" else "supplier"
        records = documents.filter(
            kind=kind,
            status__in=[
                BillingDocument.Status.ISSUED,
                BillingDocument.Status.APPROVED,
                BillingDocument.Status.PART_PAID,
                BillingDocument.Status.PAID,
            ],
        ).order_by("issue_date", "number")
        parties = list(
            documents.filter(kind=kind)
            .exclude(**{f"{party_field}__isnull": True})
            .order_by(f"{party_field}__name")
            .values_list(f"{party_field}_id", f"{party_field}__name")
            .distinct()
        )
        if party_filter.isdigit():
            records = records.filter(**{f"{party_field}_id": party_filter})
        headers = ["Date", "Reference", "Party", "Transaction", "Currency", "Debit", "Credit", "Running balance"]
        rows = _billing_ledger_rows(records, note_kind, start, end)
    elif report_key == "payment_register":
        payments = BillingPayment.objects.filter(document__factory__in=factories).select_related(
            "document__factory",
            "document__customer",
            "document__supplier",
            "recorded_by",
        ).order_by("payment_date", "pk")
        if start:
            payments = payments.filter(payment_date__gte=start)
        if end:
            payments = payments.filter(payment_date__lte=end)
        if currency:
            payments = payments.filter(document__currency_code=currency)
        headers = ["Payment date", "Document", "Kind", "Party", "Currency", "Amount", "Method", "Reference", "Recorded by"]
        rows = [[
            payment.payment_date,
            payment.document.number,
            payment.document.get_kind_display(),
            payment.document.customer.name if payment.document.customer_id else payment.document.supplier.name,
            payment.document.currency_code,
            payment.amount,
            payment.get_method_display(),
            payment.reference,
            payment.recorded_by or "",
        ] for payment in payments]
    elif report_key == "gst_summary":
        records = documents.filter(
            kind=BillingDocument.Kind.SALES_INVOICE,
            status__in=[BillingDocument.Status.ISSUED, BillingDocument.Status.PART_PAID, BillingDocument.Status.PAID],
        )
        if start:
            records = records.filter(issue_date__gte=start)
        if end:
            records = records.filter(issue_date__lte=end)
        headers = ["Date", "Reference", "Type", "Currency", "HSN/SAC", "GST rate", "Taxable value", "CGST", "SGST", "IGST", "Net GST"]
        rows = []
        for invoice in records:
            for line in invoice.lines.all():
                rows.append([invoice.issue_date, invoice.number, "Invoice", invoice.currency_code, line.hsn_sac_code, line.tax_rate, line.taxable_value, line.cgst_amount, line.sgst_amount, line.igst_amount, line.cgst_amount + line.sgst_amount + line.igst_amount])
        purchase_records = documents.filter(
            kind=BillingDocument.Kind.SUPPLIER_BILL,
            status__in=[BillingDocument.Status.APPROVED, BillingDocument.Status.PART_PAID, BillingDocument.Status.PAID],
        )
        if start:
            purchase_records = purchase_records.filter(issue_date__gte=start)
        if end:
            purchase_records = purchase_records.filter(issue_date__lte=end)
        for bill in purchase_records:
            for line in bill.lines.all():
                rows.append([bill.issue_date, bill.number, "Purchase bill · input tax", bill.currency_code, line.hsn_sac_code, line.tax_rate, -line.taxable_value, -line.cgst_amount, -line.sgst_amount, -line.igst_amount, -(line.cgst_amount + line.sgst_amount + line.igst_amount)])
        notes = CreditDebitNote.objects.filter(
            factory__in=factories,
            kind__in=[CreditDebitNote.Kind.CREDIT_NOTE, CreditDebitNote.Kind.DEBIT_NOTE],
            status=CreditDebitNote.Status.APPROVED,
            source_document__status__in=[
                BillingDocument.Status.ISSUED,
                BillingDocument.Status.APPROVED,
                BillingDocument.Status.PART_PAID,
                BillingDocument.Status.PAID,
            ],
        ).select_related("source_document").prefetch_related("lines")
        if start:
            notes = notes.filter(issue_date__gte=start)
        if end:
            notes = notes.filter(issue_date__lte=end)
        if currency:
            notes = notes.filter(currency_code=currency)
        for note in notes:
            for line in note.lines.all():
                sign = -1 if note.kind == CreditDebitNote.Kind.CREDIT_NOTE else 1
                label = "Credit note" if sign < 0 else "Debit note · input tax reversal"
                rows.append([note.issue_date, note.number, label, note.currency_code, line.hsn_sac_code, line.tax_rate, sign * line.taxable_value, sign * line.cgst_amount, sign * line.sgst_amount, sign * line.igst_amount, sign * (line.cgst_amount + line.sgst_amount + line.igst_amount)])
        rows.sort(key=lambda row: (row[0], row[1]))
    elif report_key == "outstanding":
        records = documents.filter(
            Q(kind=BillingDocument.Kind.SALES_INVOICE, status__in=[BillingDocument.Status.ISSUED, BillingDocument.Status.PART_PAID])
            | Q(kind=BillingDocument.Kind.SUPPLIER_BILL, status__in=[BillingDocument.Status.APPROVED, BillingDocument.Status.PART_PAID]),
        ).order_by("due_date", "issue_date")
        headers = ["Type", "Document", "Party", "Invoice date", "Due date", "Currency", "Original total", "Approved adjustments", "Paid", "Outstanding", "Payment status"]
        rows = [[item.get_kind_display(), item.number, item.customer.name if item.customer_id else item.supplier.name, item.issue_date, item.due_date, item.currency_code, item.total, item.approved_adjustment_total, item.amount_paid, item.balance_due, item.get_payment_status_display()] for item in records if item.balance_due > 0 and _within_report_range(item.issue_date, start, end)]
    elif report_key == "overdue":
        records = documents.filter(
            Q(kind=BillingDocument.Kind.SALES_INVOICE, status__in=[BillingDocument.Status.ISSUED, BillingDocument.Status.PART_PAID])
            | Q(kind=BillingDocument.Kind.SUPPLIER_BILL, status__in=[BillingDocument.Status.APPROVED, BillingDocument.Status.PART_PAID]),
            due_date__lt=timezone.localdate(),
        ).order_by("due_date")
        headers = ["Type", "Document", "Party", "Invoice date", "Due date", "Days overdue", "Currency", "Outstanding"]
        today = timezone.localdate()
        rows = [[item.get_kind_display(), item.number, item.customer.name if item.customer_id else item.supplier.name, item.issue_date, item.due_date, (today - item.due_date).days, item.currency_code, item.balance_due] for item in records if item.balance_due > 0 and _within_report_range(item.issue_date, start, end)]
    else:
        records = CreditDebitNote.objects.filter(factory__in=factories).select_related(
            "source_document__customer",
            "source_document__supplier",
        ).order_by("issue_date", "number")
        if start:
            records = records.filter(issue_date__gte=start)
        if end:
            records = records.filter(issue_date__lte=end)
        if currency:
            records = records.filter(currency_code=currency)
        headers = ["Date", "Note", "Type", "Original document", "Party", "Reason", "Currency", "Taxable", "CGST", "SGST", "IGST", "Tax", "Total", "Status"]
        rows = [[note.issue_date, note.number, note.get_kind_display(), note.source_document.number, note.source_document.customer.name if note.source_document.customer_id else note.source_document.supplier.name, note.reason, note.currency_code, note.taxable_value, note.cgst_amount, note.sgst_amount, note.igst_amount, note.tax_amount, note.total, note.get_status_display()] for note in records]

    fmt = request.GET.get("format")
    if fmt in ("csv", "pdf"):
        UserActivity.objects.create(
            user=request.user,
            action="billing_report_exported",
            details={
                "report": report_key,
                "format": fmt,
                "row_count": len(rows),
                "start": start.isoformat() if start else "",
                "end": end.isoformat() if end else "",
                "currency": currency,
            },
            ip_address=request.META.get("REMOTE_ADDR"),
        )
        response = export(request, title, headers, rows)
        if response:
            return response

    page_obj = Paginator(rows, 50).get_page(request.GET.get("page"))
    return render(request, "reports/billing.html", {
        "title": title,
        "report_key": report_key,
        "report_types": [(key, value[0]) for key, value in report_types.items()],
        "headers": headers,
        "page_obj": page_obj,
        "form": form,
        "currency": currency,
        "currencies": available_currencies,
        "party_filter": party_filter,
        "parties": parties,
        "can_view_billing": True,
    })


@login_required
def workforce_report(request):
    profile, companies, factories, departments, employees, attendance, leaves, quality, energy = report_scope(request.user)
    form = ReportFilterForm(request.GET or None)
    records = date_filters(request, form, attendance, "date")
    summary = records.values("employee__employee_id", "employee__first_name", "employee__last_name", "employee__factory__name", "employee__department__name").annotate(days=Count("id"), present=Count("id", filter=Q(status__in=[Attendance.Status.PRESENT, Attendance.Status.LATE, Attendance.Status.HALF_DAY])), absent=Count("id", filter=Q(status=Attendance.Status.ABSENT)), leave=Count("id", filter=Q(status=Attendance.Status.ON_LEAVE))).order_by("employee__employee_id")
    rows = [[x["employee__employee_id"], f'{x["employee__first_name"]} {x["employee__last_name"]}', x["employee__factory__name"], x["employee__department__name"], x["days"], x["present"], x["absent"], x["leave"]] for x in summary]
    response = export(request, "Workforce report", ["Employee ID", "Name", "Factory", "Department", "Days", "Present", "Absent", "On leave"], rows)
    if response: return response
    return render(request, "reports/workforce.html", {"form": form, "page_obj": Paginator(summary, 30).get_page(request.GET.get("page"))})


@login_required
def quality_energy_report(request):
    profile, companies, factories, departments, employees, attendance, leaves, quality, energy = report_scope(request.user)
    form = ReportFilterForm(request.GET or None)
    inspections = date_filters(request, form, quality, "created_at__date")
    readings = date_filters(request, form, energy, "reading_at__date")
    combined = [["Inspection", x.factory.name, x.department.name if x.department else "", x.product_reference, x.get_status_display(), x.defect_label, x.created_at] for x in inspections]
    combined += [["Energy", x.factory.name, x.department.name if x.department else "", x.meter_reference, x.meter_kwh, x.consumption_kwh or "", x.reading_at] for x in readings]
    response = export(request, "Quality and energy report", ["Type", "Factory", "Department", "Reference", "Status / meter kWh", "Defect / consumption kWh", "Recorded at"], combined)
    if response: return response
    return render(request, "reports/quality_energy.html", {"form": form, "inspections": Paginator(inspections, 20).get_page(request.GET.get("page")), "readings": Paginator(readings, 20).get_page(request.GET.get("page"))})


@login_required
def unavailable_report(request, kind):
    if kind == "production":
        profile, companies, factories, departments, employees, attendance, leaves, quality, energy = report_scope(request.user)
        entries = ProductionEntry.objects.filter(factory__in=factories).select_related("factory", "department", "production_unit", "product", "work_order")
        plans = ProductionPlan.objects.filter(factory__in=factories)
        orders = WorkOrder.objects.filter(factory__in=factories)
        if profile.role in (UserProfile.Role.DEPARTMENT_MANAGER, UserProfile.Role.EMPLOYEE):
            entries, plans, orders = entries.filter(department__in=departments), plans.filter(department__in=departments), orders.filter(department__in=departments)
        start, end = request.GET.get("start", ""), request.GET.get("end", "")
        if start:
            try: date.fromisoformat(start); entries = entries.filter(production_date__gte=start)
            except ValueError: start = ""
        if end:
            try: date.fromisoformat(end); entries = entries.filter(production_date__lte=end)
            except ValueError: end = ""
        rows = [[x.production_date, x.factory.name, x.department.name, x.production_unit.name, x.product.sku, x.work_order.order_number if x.work_order else "", x.actual_quantity, x.rejected_quantity, x.good_quantity] for x in entries]
        response = export(request, "Production report", ["Date", "Factory", "Department", "Unit", "SKU", "Work order", "Actual", "Rejected", "Good"], rows)
        if response: return response
        totals = entries.aggregate(actual=Sum("actual_quantity"), rejected=Sum("rejected_quantity"))
        return render(request, "reports/production.html", {"entries": Paginator(entries, 30).get_page(request.GET.get("page")), "plans": plans.count(), "orders": orders.count(), "actual": totals["actual"] or 0, "rejected": totals["rejected"] or 0, "start": start, "end": end})
    if kind not in ("inventory", "machines"): return redirect("reports:index")
    return render(request, "reports/unavailable.html", {"kind": kind})


@login_required
def notifications(request):
    profile, companies, factories, departments, employees, attendance, leaves, quality, energy = report_scope(request.user)
    if request.method == "POST":
        Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
        messages.success(request, "Notifications marked as read.")
        return redirect("reports:notifications")
    pending_leaves = leaves.filter(status=LeaveRequest.ApprovalStatus.PENDING)[:20] if profile.role != UserProfile.Role.EMPLOYEE else leaves.none()
    pending_quality = quality.filter(status=QualityInspection.Status.PENDING)[:20] if profile.role != UserProfile.Role.EMPLOYEE else quality.none()
    notifications = [
        {"title": notice.title, "message": notice.message, "created_at": notice.created_at,
         "target_url": notice.target_url, "is_read": notice.is_read}
        for notice in request.user.notifications.all()[:50]
    ]
    notifications.extend(
        {"title": "Leave approval needed", "message": f"{leave.employee} requested {leave.start_date} to {leave.end_date}.", "created_at": leave.created_at, "target_url": "/workforce/leave/", "is_read": False}
        for leave in pending_leaves
    )
    notifications.extend(
        {"title": "Quality review needed", "message": f"{inspection.factory.name}: {inspection.product_reference} awaits review.", "created_at": inspection.created_at, "target_url": f"/intelligence/quality/{inspection.pk}/review/", "is_read": False}
        for inspection in pending_quality
    )
    notifications.sort(key=lambda item: item["created_at"], reverse=True)
    return render(request, "reports/notifications.html", {"notifications": notifications[:50], "can_review": profile.role != UserProfile.Role.EMPLOYEE})
