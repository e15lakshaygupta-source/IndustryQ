import calendar
import csv
import html
import json
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from io import BytesIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.http import Http404, HttpResponse
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.accounts.models import UserActivity
from apps.core.pdf_utils import (
    GOLD,
    GOLD_PALE,
    INK,
    LINE,
    MUTED,
    NAVY,
    PALE,
    premium_document,
    premium_page_chrome,
    premium_pdf_styles,
    premium_table_style,
)
from apps.operations.forms import BillingLineForm
from apps.operations.models import (
    BillingDocument,
    BillingLine,
    BillingPayment,
    BillingRefund,
    CreditDebitNote,
    CreditDebitNoteLine,
    Customer,
    InventoryItem,
    Product,
)
from apps.operations.views import billing_manager, log, render_page, scope

from .forms import (
    CreditDebitNoteForm,
    InvoiceCancellationForm,
    PurchaseBillForm,
    PurchaseBillLineForm,
    SalesInvoiceForm,
)
from .tax import (
    calculate_adjustment_lines,
    calculate_gst_lines,
    calculate_purchase_gst_lines,
    effective_gst_rate,
    validate_invoice_for_issue,
)
from .validators import validate_gstin


def _invoice_for_user(request, pk, factories, profile):
    if not billing_manager(request.user, profile):
        raise Http404
    return get_object_or_404(
        BillingDocument.objects.select_related("factory", "customer").filter(
            factory__in=factories,
            kind=BillingDocument.Kind.SALES_INVOICE,
        ),
        pk=pk,
    )


def _customer_for_user(customer_id, factories):
    company_ids = factories.values_list("company_id", flat=True).distinct()
    return get_object_or_404(Customer, pk=customer_id, company_id__in=company_ids)


def _statement_rows(invoices):
    rows = []
    for invoice in invoices:
        rows.append({
            "date": invoice.issue_date,
            "sort": 0,
            "reference": invoice.number,
            "transaction": "Invoice",
            "debit": invoice.total,
            "credit": Decimal("0"),
            "currency": invoice.currency_code,
        })
        for payment in invoice.payments.all():
            rows.append({
                "date": payment.payment_date,
                "sort": 1,
                "reference": payment.receipt_number,
                "transaction": (
                    f"Payment — {payment.get_method_display()}"
                    f"{': ' + payment.method_details if payment.method_details else ''}"
                ),
                "debit": Decimal("0"),
                "credit": payment.amount,
                "currency": invoice.currency_code,
            })
            for refund in payment.refunds.all():
                rows.append({
                    "date": refund.refund_date,
                    "sort": 2,
                    "reference": refund.reference or payment.receipt_number,
                    "transaction": (
                        f"Refund — {refund.get_method_display()}"
                        f"{': ' + refund.method_details if refund.method_details else ''}"
                    ),
                    "debit": refund.amount,
                    "credit": Decimal("0"),
                    "currency": invoice.currency_code,
                })
    rows.sort(key=lambda row: (row["date"], row["sort"], row["reference"]))
    balances = defaultdict(Decimal)
    for row in rows:
        currency = row["currency"]
        balances[currency] += row["debit"] - row["credit"]
        row["balance"] = balances[currency]
    return rows


def _customer_statement_response(request, pk, download=False):
    profile, companies, factories, departments, units = scope(request.user)
    customer = _customer_for_user(pk, factories)
    invoices = list(
        customer.invoices.filter(
            factory__in=factories,
            kind=BillingDocument.Kind.SALES_INVOICE,
            status__in=[
                BillingDocument.Status.ISSUED,
                BillingDocument.Status.PART_PAID,
                BillingDocument.Status.PAID,
            ],
        )
        .select_related("factory")
        .prefetch_related("payments__refunds")
    )
    rows = _statement_rows(invoices)
    if download or request.GET.get("format") == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="customer-statement-{customer.pk}.csv"'
        writer = csv.writer(response)
        writer.writerow(["Date", "Reference", "Transaction", "Currency", "Debit", "Credit", "Balance"])
        for row in rows:
            writer.writerow([
                row["date"].isoformat(),
                row["reference"],
                row["transaction"],
                row["currency"],
                f'{row["debit"]:.2f}',
                f'{row["credit"]:.2f}',
                f'{row["balance"]:.2f}',
            ])
        return response
    return render_page(request, "billing/customer_statement.html", {
        "customer": customer,
        "rows": rows,
        "invoices": invoices,
        "outstanding_by_currency": {
            currency: sum(
                (invoice.balance_due for invoice in invoices if invoice.currency_code == currency),
                Decimal("0"),
            )
            for currency in {invoice.currency_code for invoice in invoices}
        },
    })


@login_required
def customer_statement(request, pk):
    return _customer_statement_response(request, pk)


@login_required
def customer_statement_csv(request, pk):
    return _customer_statement_response(request, pk, download=True)


def _payment_receipt_response(request, payment_id, download):
    profile, companies, factories, departments, units = scope(request.user)
    payment = get_object_or_404(
        BillingPayment.objects.select_related(
            "document__factory__company",
            "document__customer",
            "document__supplier",
            "recorded_by",
        ).filter(
            document__factory__in=factories,
            document__kind__in=[
                BillingDocument.Kind.SALES_INVOICE,
                BillingDocument.Kind.SUPPLIER_BILL,
            ],
        ),
        pk=payment_id,
    )
    invoice = payment.document
    company = invoice.factory.company
    buffer = BytesIO()
    document = premium_document(buffer, f"Payment receipt {payment.receipt_number}", company.name)
    styles = premium_pdf_styles()
    paid_at_receipt = invoice.payments.filter(
        created_at__lte=payment.created_at
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    refunded_at_receipt = (
        BillingRefund.objects.filter(
            payment__document=invoice,
            created_at__lte=payment.created_at,
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        if invoice.kind == BillingDocument.Kind.SALES_INVOICE
        else Decimal("0")
    )
    balance_at_receipt = max(
        invoice.total - paid_at_receipt + refunded_at_receipt,
        Decimal("0"),
    )
    party = (
        invoice.customer.name if invoice.customer_id
        else invoice.supplier.name if invoice.supplier_id
        else "—"
    )
    amount_label = (
        "Amount received"
        if invoice.kind == BillingDocument.Kind.SALES_INVOICE
        else "Amount paid"
    )
    direction = (
        "Received from"
        if invoice.kind == BillingDocument.Kind.SALES_INVOICE
        else "Paid to"
    )
    title_style = ParagraphStyle(
        "PaymentReceiptTitle", parent=styles["title"], fontSize=21, leading=25,
    )
    amount_style = ParagraphStyle(
        "PaymentReceiptAmount", parent=styles["title"], fontSize=24, leading=28,
        textColor=colors.white,
    )
    company_address = ", ".join(part for part in (
        invoice.factory.address or company.address,
        invoice.factory.city or company.city,
        invoice.factory.state or company.state,
    ) if part)
    details = [
        [Paragraph("RECEIPT NUMBER", styles["small"]), Paragraph("PAYMENT DATE", styles["small"])],
        [Paragraph(f"<b>{_pdf_text(payment.receipt_number)}</b>", styles["body_bold"]), Paragraph(f"<b>{payment.payment_date.strftime('%d %b %Y')}</b>", styles["body_bold"])],
        [Paragraph("INVOICE / BILL", styles["small"]), Paragraph("PAYMENT METHOD", styles["small"])],
        [Paragraph(f"<b>{_pdf_text(invoice.number)}</b>", styles["body_bold"]), Paragraph(f"<b>{_pdf_text(payment.get_method_display())}</b>", styles["body_bold"])],
        [Paragraph("REFERENCE", styles["small"]), Paragraph("RECORDED BY", styles["small"])],
        [Paragraph(_pdf_text(payment.reference or "—"), styles["body"]), Paragraph(_pdf_text(payment.recorded_by or "System"), styles["body"])],
    ]
    details_table = Table(details, colWidths=[document.width / 2, document.width / 2])
    details_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PALE),
        ("BOX", (0, 0), (-1, -1), .7, LINE),
        ("INNERGRID", (0, 0), (-1, -1), .35, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    amount_panel = Table([[
        [
            Paragraph(_pdf_text(amount_label.upper()), styles["small_white"]),
            Spacer(1, 2 * mm),
            Paragraph(_pdf_text(_format_invoice_money(payment.amount, invoice.currency_code)), amount_style),
            Paragraph(f"{direction} {_pdf_text(party)}", styles["small_white"]),
        ],
        [
            Paragraph("BALANCE AFTER PAYMENT", styles["small"]),
            Spacer(1, 2 * mm),
            Paragraph(_pdf_text(_format_invoice_money(balance_at_receipt, invoice.currency_code)), styles["body_bold"]),
        ],
    ]], colWidths=[document.width * .64, document.width * .36])
    amount_panel.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), NAVY),
        ("BACKGROUND", (1, 0), (1, 0), GOLD_PALE),
        ("BOX", (0, 0), (-1, -1), .7, GOLD),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
    ]))
    story = [
        Paragraph("Payment receipt", title_style),
        Paragraph(_pdf_text(company.name), styles["subtitle"]),
        Spacer(1, 5 * mm),
        amount_panel,
        Spacer(1, 5 * mm),
        Paragraph("Payment details", styles["body_bold"]),
        Spacer(1, 2 * mm),
        details_table,
    ]
    if payment.method_details:
        story.extend([
            Spacer(1, 3 * mm),
            Paragraph(f"<b>Method details:</b> {_pdf_text(payment.method_details)}", styles["body"]),
        ])
    if company_address or company.phone or company.email:
        contact = "  |  ".join(
            value for value in (company_address, company.phone, company.email) if value
        )
        story.extend([
            Spacer(1, 8 * mm),
            Paragraph(_pdf_text(contact), styles["small"]),
        ])
    document.build(
        story,
        onFirstPage=lambda canvas, doc: premium_page_chrome(canvas, doc, "Payment receipt", company.name),
        onLaterPages=lambda canvas, doc: premium_page_chrome(canvas, doc, "Payment receipt", company.name),
    )
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    disposition = "attachment" if download else "inline"
    response["Content-Disposition"] = (
        f'{disposition}; filename="{payment.receipt_number}.pdf"'
    )
    return response


@login_required
def payment_receipt(request, payment_id):
    return _payment_receipt_response(request, payment_id, download=False)


@login_required
def payment_receipt_download(request, payment_id):
    return _payment_receipt_response(request, payment_id, download=True)


def _log_invoice_transition(request, invoice, action, previous_status, reason="", extra_details=None):
    details = {
        "record_id": invoice.pk,
        "invoice_number": invoice.number,
        "from_status": previous_status,
        "to_status": invoice.status,
    }
    if extra_details:
        details.update(extra_details)
    if reason:
        details["cancellation_reason"] = reason
    UserActivity.objects.create(
        user=request.user,
        action=action,
        details=details,
        ip_address=request.META.get("REMOTE_ADDR"),
    )


def _purchase_bill_for_user(request, pk, factories, profile):
    if not billing_manager(request.user, profile):
        raise Http404
    return get_object_or_404(
        BillingDocument.objects.select_related(
            "factory__company",
            "supplier",
            "procurement_document",
        ).filter(factory__in=factories, kind=BillingDocument.Kind.SUPPLIER_BILL),
        pk=pk,
    )


def _adjustment_source_for_user(pk, factories, profile, user):
    if not billing_manager(user, profile):
        raise Http404
    return get_object_or_404(
        BillingDocument.objects.select_related("factory", "customer", "supplier")
        .filter(factory__in=factories),
        pk=pk,
    )


def _adjustment_source_is_open(source):
    if source.kind == BillingDocument.Kind.SALES_INVOICE:
        return source.status in (
            BillingDocument.Status.ISSUED,
            BillingDocument.Status.PART_PAID,
            BillingDocument.Status.PAID,
        )
    return source.status in (
        BillingDocument.Status.APPROVED,
        BillingDocument.Status.PART_PAID,
        BillingDocument.Status.PAID,
    )


@login_required
def credit_debit_note_create(request, document_id):
    profile, companies, factories, departments, units = scope(request.user)
    source = _adjustment_source_for_user(document_id, factories, profile, request.user)
    if not _adjustment_source_is_open(source):
        messages.error(request, "Only verified or issued invoices can have return adjustments.")
        return redirect("billing:document_detail", pk=source.pk)

    form = CreditDebitNoteForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if form.cleaned_data["issue_date"] < source.issue_date:
            form.add_error("issue_date", "Adjustment date cannot be before the original invoice date.")
        try:
            payload = json.loads(request.POST.get("lines", "[]"))
            if not isinstance(payload, list) or not payload:
                raise ValueError
            requested_quantities = []
            seen_line_ids = set()
            for entry in payload:
                if not isinstance(entry, dict):
                    raise ValueError
                line_id = int(entry["source_line"])
                quantity = Decimal(str(entry["quantity"]))
                if line_id in seen_line_ids:
                    raise ValueError
                seen_line_ids.add(line_id)
                requested_quantities.append((line_id, quantity))
        except (KeyError, TypeError, ValueError, InvalidOperation):
            requested_quantities = []
            form.add_error(None, "Add at least one valid return item.")

        if requested_quantities and not form.errors:
            with transaction.atomic():
                factory = factories.select_for_update().get(pk=source.factory_id)
                source = BillingDocument.objects.select_for_update().get(pk=source.pk)
                if not _adjustment_source_is_open(source):
                    form.add_error(None, "The original invoice is no longer eligible for adjustment.")
                else:
                    source_lines = {
                        line.pk: line
                        for line in source.lines.filter(
                            pk__in=[line_id for line_id, _ in requested_quantities]
                        )
                    }
                    if len(source_lines) != len(requested_quantities):
                        form.add_error(None, "Each return item must be from the selected original invoice.")
                    else:
                        line_requests = [
                            (source_lines[line_id], quantity)
                            for line_id, quantity in requested_quantities
                        ]
                        try:
                            adjustment_values = calculate_adjustment_lines(source, line_requests)
                        except ValidationError as error:
                            form.add_error(None, error)
                        else:
                            kind = (
                                CreditDebitNote.Kind.CREDIT_NOTE
                                if source.kind == BillingDocument.Kind.SALES_INVOICE
                                else CreditDebitNote.Kind.DEBIT_NOTE
                            )
                            prefix = "CN" if kind == CreditDebitNote.Kind.CREDIT_NOTE else "DN"
                            stem = f"{prefix}-{factory.factory_code}-{form.cleaned_data['issue_date']:%y}-"
                            latest = CreditDebitNote.objects.filter(
                                factory=factory,
                                kind=kind,
                                number__startswith=stem,
                            ).order_by("-number").values_list("number", flat=True).first()
                            sequence = int(latest.rsplit("-", 1)[-1]) + 1 if latest else 1
                            note = CreditDebitNote.objects.create(
                                factory=factory,
                                source_document=source,
                                kind=kind,
                                number=f"{stem}{sequence:05d}",
                                issue_date=form.cleaned_data["issue_date"],
                                reason=form.cleaned_data["reason"],
                                currency_code=source.currency_code,
                                created_by=request.user,
                                **{
                                    field: adjustment_values[field]
                                    for field in (
                                        "subtotal",
                                        "discount_amount",
                                        "taxable_value",
                                        "cgst_amount",
                                        "sgst_amount",
                                        "igst_amount",
                                        "tax_amount",
                                        "total",
                                    )
                                },
                            )
                            for line in adjustment_values["lines"]:
                                CreditDebitNoteLine.objects.create(note=note, **line)
                            UserActivity.objects.create(
                                user=request.user,
                                action="billing_adjustment_created",
                                details={
                                    "record_id": note.pk,
                                    "adjustment_number": note.number,
                                    "source_document": source.number,
                                    "reason": note.reason,
                                },
                                ip_address=request.META.get("REMOTE_ADDR"),
                            )
                            messages.success(
                                request,
                                f"Draft {note.get_kind_display().lower()} {note.number} created.",
                            )
                            return redirect("billing:adjustment_detail", pk=note.pk)

    available_lines = []
    for line in source.lines.all():
        approved_quantity = line.adjustment_lines.filter(
            note__status=CreditDebitNote.Status.APPROVED
        ).aggregate(quantity=Sum("quantity"))["quantity"] or Decimal("0")
        remaining_quantity = line.quantity - approved_quantity
        if remaining_quantity > 0:
            available_lines.append({
                "id": line.pk,
                "description": line.description,
                "quantity": remaining_quantity,
                "unit_price": line.unit_price,
                "tax_rate": line.tax_rate,
                "hsn_sac_code": line.hsn_sac_code,
            })
    return render_page(request, "billing/adjustment_form.html", {
        "form": form,
        "source": source,
        "available_lines": available_lines,
        "note_label": (
            "Credit note"
            if source.kind == BillingDocument.Kind.SALES_INVOICE
            else "Debit note"
        ),
        "title": "Create return adjustment",
    })


@login_required
def credit_debit_note_list(request):
    profile, companies, factories, departments, units = scope(request.user)
    notes = CreditDebitNote.objects.filter(factory__in=factories).select_related(
        "source_document__customer",
        "source_document__supplier",
        "factory",
        "created_by",
        "approved_by",
    )
    return render_page(request, "billing/adjustment_list.html", {
        "notes": notes[:100],
        "can_manage": billing_manager(request.user, profile),
        "title": "Credit and debit note history",
    })


@login_required
def credit_debit_note_detail(request, pk):
    profile, companies, factories, departments, units = scope(request.user)
    note = get_object_or_404(
        CreditDebitNote.objects.select_related(
            "factory",
            "source_document__customer",
            "source_document__supplier",
            "created_by",
            "approved_by",
        ).prefetch_related("lines__source_line"),
        pk=pk,
        factory__in=factories,
    )
    return render_page(request, "billing/adjustment_detail.html", {
        "note": note,
        "can_manage": billing_manager(request.user, profile),
        "title": note.get_kind_display(),
    })


@login_required
@require_POST
def credit_debit_note_approve(request, pk):
    profile, companies, factories, departments, units = scope(request.user)
    if not billing_manager(request.user, profile):
        raise Http404
    with transaction.atomic():
        note = get_object_or_404(
            CreditDebitNote.objects.select_related("source_document")
            .filter(factory__in=factories)
            .select_for_update(),
            pk=pk,
        )
        source = BillingDocument.objects.select_for_update().get(pk=note.source_document_id)
        if note.status != CreditDebitNote.Status.DRAFT:
            messages.error(request, "Only draft adjustments can be approved.")
        elif not _adjustment_source_is_open(source):
            messages.error(request, "The original invoice is no longer eligible for adjustment.")
        else:
            line_requests = [
                (line.source_line, line.quantity)
                for line in note.lines.select_related("source_line")
            ]
            try:
                values = calculate_adjustment_lines(source, line_requests)
            except ValidationError as error:
                messages.error(request, " ".join(error.messages))
            else:
                for field in (
                    "subtotal",
                    "discount_amount",
                    "taxable_value",
                    "cgst_amount",
                    "sgst_amount",
                    "igst_amount",
                    "tax_amount",
                    "total",
                ):
                    setattr(note, field, values[field])
                note.status = CreditDebitNote.Status.APPROVED
                note.approved_by = request.user
                note.approved_at = timezone.now()
                note.save()
                for line, values_line in zip(note.lines.all(), values["lines"]):
                    for field in (
                        "line_total",
                        "discount_amount",
                        "taxable_value",
                        "cgst_amount",
                        "sgst_amount",
                        "igst_amount",
                    ):
                        setattr(line, field, values_line[field])
                    line.save(update_fields=[
                        "line_total",
                        "discount_amount",
                        "taxable_value",
                        "cgst_amount",
                        "sgst_amount",
                        "igst_amount",
                    ])
                UserActivity.objects.create(
                    user=request.user,
                    action="billing_adjustment_approved",
                    details={
                        "record_id": note.pk,
                        "adjustment_number": note.number,
                        "source_document": source.number,
                        "total": str(note.total),
                        "from_status": CreditDebitNote.Status.DRAFT,
                        "to_status": note.status,
                    },
                    ip_address=request.META.get("REMOTE_ADDR"),
                )
                messages.success(request, f"Adjustment {note.number} approved.")
    return redirect("billing:adjustment_detail", pk=note.pk)


@login_required
def purchase_bill_create(request):
    profile, companies, factories, departments, units = scope(request.user)
    if not billing_manager(request.user, profile):
        raise Http404
    form = PurchaseBillForm(
        request.POST or None,
        request.FILES or None,
        factories=factories,
    )
    line_forms = []
    if request.method == "POST" and form.is_valid():
        try:
            payload = json.loads(request.POST.get("lines", "[]"))
            if not isinstance(payload, list) or not payload or any(
                not isinstance(line, dict) for line in payload
            ):
                raise ValueError
        except (ValueError, TypeError):
            payload = []
            form.add_error(None, "Add at least one valid purchase bill line.")

        validated_lines = []
        if payload and form.is_valid():
            for line_data in payload:
                line_form = PurchaseBillLineForm(line_data, factories=factories)
                if line_form.is_valid():
                    validated_lines.append(line_form.cleaned_data)
                else:
                    line_forms.append(line_form)
                    form.add_error(None, "Check the purchase bill line details.")

        if payload and len(validated_lines) == len(payload) and form.is_valid():
            bill = form.save(commit=False)
            bill.kind = BillingDocument.Kind.SUPPLIER_BILL
            bill.status = BillingDocument.Status.DRAFT
            bill.created_by = request.user
            for line in validated_lines:
                inventory_item = line.get("inventory_item")
                if inventory_item and inventory_item.factory_id != bill.factory_id:
                    form.add_error(None, "Inventory items must belong to the selected factory.")
                    break
                procurement_document = bill.procurement_document
                if (
                    inventory_item
                    and procurement_document
                    and inventory_item.pk != procurement_document.item_id
                ):
                    form.add_error(
                        None,
                        "A linked purchase order bill must use the order's inventory item.",
                    )
                    break
            subtotal = sum(
                (line["quantity"] * line["unit_price"] for line in validated_lines),
                Decimal("0"),
            ).quantize(Decimal("0.01"))
            if bill.discount_amount > subtotal:
                form.add_error("discount_amount", "Discount cannot exceed the bill subtotal.")
            if not form.errors:
                try:
                    gst = calculate_purchase_gst_lines(
                        validated_lines,
                        bill.discount_amount,
                        bill.issue_date,
                        bill,
                    )
                except ValidationError as error:
                    form.add_error(None, error)
                else:
                    with transaction.atomic():
                        factory = factories.select_for_update().get(pk=bill.factory_id)
                        bill.factory = factory
                        bill.number = BillingDocument.next_number(
                            factory,
                            BillingDocument.Kind.SUPPLIER_BILL,
                            bill.issue_date,
                        )
                        for field, value in gst.items():
                            if field not in {"lines", "taxable_value"}:
                                setattr(bill, field, value)
                        bill.save()
                        for line in gst["lines"]:
                            BillingLine.objects.create(
                                document=bill,
                                description=line["description"],
                                product=line["product"],
                                inventory_item=line["inventory_item"],
                                quantity=line["quantity"],
                                unit_price=line["unit_price"],
                                line_total=line["line_total"],
                                discount_amount=line["discount_amount"],
                                taxable_value=line["taxable_value"],
                                tax_rate=line["tax_rate"],
                                hsn_sac_code=line["hsn_sac_code"],
                                cgst_amount=line["cgst_amount"],
                                sgst_amount=line["sgst_amount"],
                                igst_amount=line["igst_amount"],
                            )
                        _log_invoice_transition(
                            request,
                            bill,
                            "purchase_bill_registered",
                            "",
                        )
                    messages.success(request, f"Draft purchase bill {bill.number} registered.")
                    return redirect("billing:document_detail", pk=bill.pk)

    products = Product.objects.filter(is_active=True).select_related("gst_tax_profile")
    inventory_items = InventoryItem.objects.filter(
        factory__in=factories,
        is_active=True,
    ).select_related("factory")
    product_options = []
    for product in products:
        try:
            rate = effective_gst_rate(product, form.fields["issue_date"].initial)
        except ValidationError:
            rate = product.tax_rate
        product_options.append({
            "id": product.pk,
            "label": f"{product.sku} - {product.name}",
            "rate": rate,
            "hsn_sac_code": product.hsn_sac_code,
        })
    inventory_options = [
        {
            "id": item.pk,
            "label": f"{item.sku} - {item.name} ({item.factory.name})",
            "factory_id": item.factory_id,
            "unit": item.unit_of_measure,
        }
        for item in inventory_items
    ]
    return render_page(request, "billing/purchase_bill_form.html", {
        "form": form,
        "line_forms": line_forms,
        "product_options": product_options,
        "inventory_options": inventory_options,
        "title": "Register supplier bill",
    })


@login_required
@require_POST
def purchase_bill_verify(request, pk):
    profile, companies, factories, departments, units = scope(request.user)
    bill = _purchase_bill_for_user(request, pk, factories, profile)
    with transaction.atomic():
        bill = BillingDocument.objects.select_for_update().get(pk=bill.pk)
        if bill.status != BillingDocument.Status.DRAFT:
            messages.error(request, "Only draft supplier bills can be verified.")
        elif not bill.lines.exists():
            messages.error(request, "A supplier bill must contain at least one line before verification.")
        elif bill.tax_amount > 0 and not bill.supplier_gstin:
            messages.error(request, "A taxable supplier bill must include the supplier GSTIN.")
        else:
            if bill.supplier_gstin:
                try:
                    validate_gstin(bill.supplier_gstin)
                except ValidationError as error:
                    messages.error(request, " ".join(error.messages))
                    return redirect("billing:document_detail", pk=bill.pk)
            previous_status = bill.status
            bill.status = BillingDocument.Status.APPROVED
            bill.approved_by = request.user
            bill.approved_at = timezone.now()
            bill.save(update_fields=["status", "approved_by", "approved_at"])
            _log_invoice_transition(request, bill, "purchase_bill_verified", previous_status)
            messages.success(request, f"Supplier bill {bill.number} verified.")
    return redirect("billing:document_detail", pk=bill.pk)


def purchase_bill_pdf(request, pk, download=False):
    profile, companies, factories, departments, units = scope(request.user)
    bill = get_object_or_404(
        BillingDocument.objects.select_related(
            "factory__company",
            "supplier",
            "procurement_document",
        ).prefetch_related("lines__inventory_item", "lines__product").filter(
            factory__in=factories,
            kind=BillingDocument.Kind.SUPPLIER_BILL,
        ),
        pk=pk,
    )
    buffer = BytesIO()
    company = bill.factory.company
    document = premium_document(buffer, f"Purchase bill {bill.number}", company.name)
    styles = premium_pdf_styles()
    content_width = document.width
    half_width = (content_width - 5 * mm) / 2
    def date_text(value):
        return value.strftime("%d %b %Y") if value else "Not set"
    company_address = ", ".join(part for part in (
        bill.factory.address or company.address,
        bill.factory.city or company.city,
        bill.factory.state or company.state,
        company.country,
    ) if part)
    company_details = [
        Paragraph(_pdf_text(company.name), styles["title"]),
        Paragraph(_pdf_text(bill.factory.name), styles["small"]),
    ]
    if company_address:
        company_details.append(Paragraph(_pdf_text(company_address), styles["small"]))
    if company.phone or company.email:
        company_details.append(Paragraph(
            _pdf_text("  |  ".join(value for value in (company.phone, company.email) if value)),
            styles["small"],
        ))
    if bill.customer_gstin or bill.factory.gstin:
        company_details.append(Paragraph(
            f"<b>GSTIN:</b> {_pdf_text(bill.customer_gstin or bill.factory.gstin)}",
            styles["small"],
        ))
    meta = [
        Paragraph("PURCHASE BILL", ParagraphStyle(
            "PurchaseBillHeading", parent=styles["title"], fontSize=15,
            leading=18, textColor=colors.white, alignment=TA_RIGHT,
        )),
        Spacer(1, 2 * mm),
        Paragraph(f"<b>Internal No.</b> &nbsp; {_pdf_text(bill.number)}", styles["small_white"]),
        Paragraph(f"<b>Supplier invoice</b> &nbsp; {_pdf_text(bill.supplier_invoice_number or '—')}", styles["small_white"]),
        Paragraph(f"<b>Date</b> &nbsp; {date_text(bill.issue_date)}", styles["small_white"]),
        Paragraph(f"<b>Due</b> &nbsp; {date_text(bill.due_date)}", styles["small_white"]),
        Paragraph(f"<b>Status</b> &nbsp; {_pdf_text(bill.get_status_display())}", styles["small_white"]),
    ]
    header = Table([[company_details, meta]], colWidths=[content_width * .58, content_width * .42])
    header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), colors.white),
        ("BACKGROUND", (1, 0), (1, 0), NAVY),
        ("BOX", (0, 0), (-1, -1), .7, GOLD),
        ("LINEBEFORE", (1, 0), (1, -1), 2, GOLD),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))

    def party_card(title, name, address, gstin):
        body = [Paragraph(_pdf_text(name or "—"), styles["body_bold"])]
        if address:
            body.append(Paragraph(_pdf_text(address).replace("\n", "<br/>"), styles["body"]))
        if gstin:
            body.append(Paragraph(f"<b>GSTIN:</b> {_pdf_text(gstin)}", styles["body"]))
        card = Table([[Paragraph(title, styles["section"])], [body]], colWidths=[half_width])
        card.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("BACKGROUND", (0, 1), (-1, 1), PALE),
            ("BOX", (0, 0), (-1, -1), .65, GOLD),
            ("LINEBELOW", (0, 0), (-1, 0), 1, GOLD),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]))
        return card

    parties = Table([[
        party_card("SUPPLIER", bill.supplier.name if bill.supplier_id else "", bill.supplier.address if bill.supplier_id else "", bill.supplier_gstin or (bill.supplier.gstin if bill.supplier_id else "")),
        party_card("BILL RECEIVED BY", company.name, company_address, bill.customer_gstin or bill.factory.gstin),
    ]], colWidths=[half_width, half_width])
    parties.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
        ("RIGHTPADDING", (0, 0), (0, -1), 2.5 * mm),
        ("LEFTPADDING", (1, 0), (1, -1), 2.5 * mm),
        ("RIGHTPADDING", (1, 0), (1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))

    rows = [[
        Paragraph("#", styles["section"]),
        Paragraph("Description", styles["section"]),
        Paragraph("Item / HSN", styles["section"]),
        Paragraph("Qty", styles["section"]),
        Paragraph("Taxable", styles["section"]),
        Paragraph("GST", styles["section"]),
        Paragraph("Input tax", styles["section"]),
        Paragraph("Total", styles["section"]),
    ]]
    for index, line in enumerate(bill.lines.all(), start=1):
        reference = line.inventory_item.sku if line.inventory_item_id else line.hsn_sac_code or "—"
        input_tax = line.cgst_amount + line.sgst_amount + line.igst_amount
        rows.append([
            str(index),
            Paragraph(_pdf_text(line.description).replace("\n", "<br/>"), styles["body"]),
            _pdf_text(reference),
            f"{line.quantity:,.3f}".rstrip("0").rstrip("."),
            _pdf_text(_format_invoice_money(line.taxable_value, bill.currency_code)),
            f"{line.tax_rate}%",
            _pdf_text(_format_invoice_money(input_tax, bill.currency_code)),
            _pdf_text(_format_invoice_money(line.gross_total, bill.currency_code)),
        ])
    table = Table(
        rows,
        colWidths=[8 * mm, 53 * mm, 20 * mm, 15 * mm, 27 * mm, 15 * mm, 29 * mm, content_width - 167 * mm],
        repeatRows=1,
    )
    table.setStyle(premium_table_style())
    table.setStyle(TableStyle([
        ("ALIGN", (0, 1), (0, -1), "CENTER"),
        ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))

    summary_rows = []
    for label, amount in (
        ("Subtotal", bill.subtotal),
        ("Discount", -bill.discount_amount),
        ("Taxable value", bill.taxable_value),
        ("CGST input", bill.cgst_amount),
        ("SGST input", bill.sgst_amount),
        ("IGST input", bill.igst_amount),
        ("TOTAL AMOUNT", bill.total),
    ):
        if label == "Discount" and not bill.discount_amount:
            continue
        summary_rows.append([
            Paragraph(label, styles["body_bold"] if label in ("Subtotal", "Taxable value", "TOTAL AMOUNT") else styles["body"]),
            Paragraph(_pdf_text(_format_invoice_money(abs(amount), bill.currency_code)), styles["right"]),
        ])
    summary = Table(summary_rows, colWidths=[43 * mm, 47 * mm], hAlign="RIGHT")
    summary.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -2), PALE),
        ("BACKGROUND", (0, -1), (-1, -1), GOLD_PALE),
        ("GRID", (0, 0), (-1, -2), .35, LINE),
        ("BOX", (0, 0), (-1, -1), .7, GOLD),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story = [
        header, Spacer(1, 4 * mm), parties, Spacer(1, 4 * mm), table,
        Spacer(1, 3 * mm), summary,
    ]
    if bill.notes:
        story.extend([
            Spacer(1, 4 * mm),
            Paragraph("<b>Notes</b>", styles["body_bold"]),
            Paragraph(_pdf_text(bill.notes).replace("\n", "<br/>"), styles["body"]),
        ])
    if bill.approved_at:
        story.extend([
            Spacer(1, 2 * mm),
            Paragraph(
                f"Verified by {_pdf_text(bill.approved_by or 'Deleted user')} on "
                f"{_pdf_text(timezone.localtime(bill.approved_at).strftime('%d %b %Y, %I:%M %p'))}.",
                styles["small"],
            ),
        ])
    document.build(
        story,
        onFirstPage=lambda canvas, doc: premium_page_chrome(canvas, doc, "Purchase bill", company.name),
        onLaterPages=lambda canvas, doc: premium_page_chrome(canvas, doc, "Purchase bill", company.name),
    )
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    disposition = "attachment" if download else "inline"
    response["Content-Disposition"] = f'{disposition}; filename="purchase-bill-{bill.number}.pdf"'
    return response


@login_required
def purchase_bill_pdf_print(request, pk):
    return purchase_bill_pdf(request, pk)


@login_required
def purchase_bill_pdf_download(request, pk):
    return purchase_bill_pdf(request, pk, download=True)


@login_required
def purchase_bill_source_pdf(request, pk):
    profile, companies, factories, departments, units = scope(request.user)
    bill = _purchase_bill_for_user(request, pk, factories, profile)
    if not bill.supplier_invoice_pdf:
        raise Http404
    response = FileResponse(
        bill.supplier_invoice_pdf.open("rb"),
        content_type="application/pdf",
        as_attachment=True,
        filename=f"supplier-invoice-{bill.number}.pdf",
    )
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
def invoice_create(request, pk=None):
    profile, companies, factories, departments, units = scope(request.user)
    if not billing_manager(request.user, profile):
        raise Http404
    invoice = None
    if pk is not None:
        invoice = _invoice_for_user(request, pk, factories, profile)
        if invoice.status != BillingDocument.Status.DRAFT:
            messages.error(request, "Only draft invoices can be edited.")
            return redirect("billing:document_detail", pk=invoice.pk)
    form_factories = factories.filter(pk=invoice.factory_id) if invoice else factories
    form = SalesInvoiceForm(request.POST or None, factories=form_factories, instance=invoice)
    line_forms = []
    if request.method == "POST" and form.is_valid():
        try:
            payload = json.loads(request.POST.get("lines", "[]"))
            if not isinstance(payload, list) or not payload:
                raise ValueError
        except (ValueError, TypeError):
            payload = []
            form.add_error(None, "Add at least one invoice line.")

        validated_lines = []
        if payload and form.is_valid():
            for line_data in payload:
                if not isinstance(line_data, dict):
                    form.add_error(None, "Check the invoice line details.")
                    continue
                line_form = BillingLineForm(line_data)
                if line_form.is_valid():
                    validated_lines.append(line_form.cleaned_data)
                else:
                    line_forms.append(line_form)
                    form.add_error(None, "Check the invoice line details.")

        if payload and len(validated_lines) == len(payload) and form.is_valid():
            edited_invoice = form.save(commit=False)
            edited_invoice.kind = BillingDocument.Kind.SALES_INVOICE
            try:
                gst = calculate_gst_lines(
                    validated_lines,
                    form.cleaned_data["discount_amount"],
                    edited_invoice.issue_date,
                    edited_invoice,
                )
            except ValidationError as error:
                form.add_error(None, error)
            else:
                with transaction.atomic():
                    factory = factories.select_for_update().get(pk=edited_invoice.factory_id)
                    if invoice:
                        locked_invoice = BillingDocument.objects.select_for_update().get(pk=invoice.pk)
                        if locked_invoice.status != BillingDocument.Status.DRAFT:
                            form.add_error(None, "This invoice is no longer a draft and cannot be edited.")
                            messages.error(request, "This invoice is no longer a draft and cannot be edited.")
                            return redirect("billing:document_detail", pk=invoice.pk)
                    edited_invoice.factory = factory
                    edited_invoice.status = BillingDocument.Status.DRAFT
                    if not invoice:
                        edited_invoice.created_by = request.user
                        edited_invoice.number = BillingDocument.next_number(factory, edited_invoice.kind, edited_invoice.issue_date)
                    for field, value in gst.items():
                        if field not in {"lines", "taxable_value"}:
                            setattr(edited_invoice, field, value)
                    edited_invoice.save()
                    if invoice:
                        edited_invoice.lines.all().delete()
                    for line in gst["lines"]:
                        BillingLine.objects.create(
                            document=edited_invoice,
                            description=line["description"],
                            product=line["product"],
                            quantity=line["quantity"],
                            unit_price=line["unit_price"],
                            line_total=line["line_total"],
                            discount_amount=line["discount_amount"],
                            taxable_value=line["taxable_value"],
                            tax_rate=line["tax_rate"],
                            hsn_sac_code=line["hsn_sac_code"],
                            cgst_amount=line["cgst_amount"],
                            sgst_amount=line["sgst_amount"],
                            igst_amount=line["igst_amount"],
                        )
                    log(request, "sales_invoice_updated" if invoice else "sales_invoice_created", edited_invoice)
                messages.success(request, f"Draft invoice {edited_invoice.number} saved.")
                return redirect("billing:document_detail", pk=edited_invoice.pk)

    products = Product.objects.filter(is_active=True).select_related("gst_tax_profile")
    issue_date = (
        form.cleaned_data["issue_date"]
        if form.is_bound and form.is_valid()
        else form.fields["issue_date"].initial
    )
    product_options = []
    for product in products:
        try:
            rate = effective_gst_rate(product, issue_date)
        except ValidationError:
            rate = None
        product_options.append({
            "id": product.pk,
            "label": f"{product.sku} - {product.name}",
            "name": product.name,
            "price": product.selling_price,
            "rate": rate,
            "hsn_sac_code": product.hsn_sac_code,
        })
    customer_states = [
        {"id": customer.pk, "state_code": customer.state_code or customer.tax_registration_number[:2]}
        for customer in form.fields["customer"].queryset
    ]
    factory_states = [
        {"id": factory.pk, "state_code": factory.state_code or factory.gstin[:2]}
        for factory in form.fields["factory"].queryset
    ]
    return render_page(
        request,
        "billing/invoice_form.html",
        {
            "form": form,
            "line_forms": line_forms,
            "product_options": product_options,
            "customer_states": customer_states,
            "factory_states": factory_states,
            "existing_lines": [
                {"description": line.description, "product": line.product_id or "", "quantity": str(line.quantity), "unit_price": str(line.unit_price), "hsn_sac_code": line.hsn_sac_code, "tax_rate": str(line.tax_rate)}
                for line in invoice.lines.all()
            ] if invoice else [],
            "title": "Edit sales invoice" if invoice else "Create sales invoice",
        },
    )


@login_required
@require_POST
def invoice_approve(request, pk):
    profile, companies, factories, departments, units = scope(request.user)
    invoice = _invoice_for_user(request, pk, factories, profile)
    with transaction.atomic():
        invoice = BillingDocument.objects.select_for_update().get(pk=invoice.pk)
        if invoice.status != BillingDocument.Status.DRAFT:
            messages.error(request, "Only draft invoices can be approved.")
        else:
            previous_status = invoice.status
            invoice.status = BillingDocument.Status.APPROVED
            invoice.approved_by = request.user
            invoice.approved_at = timezone.now()
            invoice.save(update_fields=["status", "approved_by", "approved_at"])
            _log_invoice_transition(request, invoice, "sales_invoice_approved", previous_status)
            messages.success(request, f"Invoice {invoice.number} approved.")
    return redirect("billing:document_detail", pk=invoice.pk)


@login_required
@require_POST
def invoice_issue(request, pk):
    profile, companies, factories, departments, units = scope(request.user)
    invoice = _invoice_for_user(request, pk, factories, profile)
    with transaction.atomic():
        invoice = BillingDocument.objects.select_for_update().get(pk=invoice.pk)
        if invoice.status != BillingDocument.Status.APPROVED:
            messages.error(request, "Approve the draft before issuing the invoice.")
        elif request.POST.get("confirm_legal_accuracy") != "on":
            messages.error(request, "Confirm that the invoice, customer, address, GSTIN, HSN/SAC, and tax details were checked against source records.")
        else:
            try:
                validate_invoice_for_issue(invoice)
            except ValidationError as error:
                messages.error(request, " ".join(error.messages))
            else:
                previous_status = invoice.status
                invoice.status = BillingDocument.Status.ISSUED
                invoice.issued_by = request.user
                invoice.issued_at = timezone.now()
                invoice.supplier_gstin = invoice.factory.gstin.strip().upper()
                invoice.customer_gstin = invoice.customer.tax_registration_number.strip().upper()
                invoice.save(update_fields=[
                    "status", "issued_by", "issued_at", "supplier_gstin", "customer_gstin",
                ])
                _log_invoice_transition(
                    request,
                    invoice,
                    "sales_invoice_issued",
                    previous_status,
                    extra_details={
                        "legal_invoice_details_confirmed": True,
                        "seller_gstin": invoice.supplier_gstin,
                        "customer_gstin": invoice.customer_gstin,
                        "place_of_supply_state_code": invoice.place_of_supply_state_code,
                    },
                )
                messages.success(request, f"Invoice {invoice.number} issued.")
    return redirect("billing:document_detail", pk=invoice.pk)


@login_required
@require_POST
def invoice_cancel(request, pk):
    profile, companies, factories, departments, units = scope(request.user)
    invoice = _invoice_for_user(request, pk, factories, profile)
    form = InvoiceCancellationForm(request.POST)
    with transaction.atomic():
        invoice = BillingDocument.objects.select_for_update().get(pk=invoice.pk)
        if not form.is_valid():
            messages.error(request, "A cancellation reason is required.")
        elif invoice.status in (
            BillingDocument.Status.PAID,
            BillingDocument.Status.PART_PAID,
            BillingDocument.Status.VOID,
        ):
            messages.error(request, "Paid, partially paid, or already cancelled invoices cannot be cancelled.")
        elif invoice.adjustments.filter(status=CreditDebitNote.Status.APPROVED).exists():
            messages.error(request, "An invoice with an approved credit note cannot be cancelled.")
        else:
            previous_status = invoice.status
            invoice.status = BillingDocument.Status.VOID
            invoice.canceled_by = request.user
            invoice.canceled_at = timezone.now()
            invoice.cancellation_reason = form.cleaned_data["reason"]
            invoice.save(update_fields=["status", "canceled_by", "canceled_at", "cancellation_reason"])
            _log_invoice_transition(
                request,
                invoice,
                "sales_invoice_cancelled",
                previous_status,
                reason=invoice.cancellation_reason,
            )
            messages.success(request, f"Invoice {invoice.number} cancelled.")
    return redirect("billing:document_detail", pk=invoice.pk)


def _pdf_text(value):
    return html.escape(str(value).encode("latin-1", "replace").decode("latin-1"))


def _number_to_indian_words(number):
    number = int(number)
    if number == 0:
        return "Zero"
    ones = (
        "Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven",
        "Eight", "Nine", "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen",
        "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen",
    )
    tens = ("", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety")

    def below_thousand(value):
        words = []
        if value >= 100:
            words.extend((ones[value // 100], "Hundred"))
            value %= 100
        if value >= 20:
            words.append(tens[value // 10])
            value %= 10
        if value:
            words.append(ones[value])
        return " ".join(words)

    parts = []
    for divisor, label in ((10000000, "Crore"), (100000, "Lakh"), (1000, "Thousand")):
        amount, number = divmod(number, divisor)
        if amount:
            parts.extend((below_thousand(amount), label))
    if number:
        parts.append(below_thousand(number))
    return " ".join(parts)


def _amount_in_words(amount, currency_code):
    amount = Decimal(amount).quantize(Decimal("0.01"))
    whole = int(amount)
    fraction = int((amount - whole) * 100)
    if currency_code == "INR":
        result = f"Rupees {_number_to_indian_words(whole)}"
        if fraction:
            result += f" and Paise {_number_to_indian_words(fraction)}"
    else:
        result = f"{currency_code} {_number_to_indian_words(whole)}"
        if fraction:
            result += f" and {currency_code} {_number_to_indian_words(fraction)} cents"
    return f"{result} Only"


def _format_invoice_money(amount, currency_code):
    amount = Decimal(amount).quantize(Decimal("0.01"))
    whole, fraction = f"{amount:,.2f}".split(".")
    if currency_code == "INR":
        digits = whole.replace(",", "")
        if len(digits) > 3:
            tail = digits[-3:]
            leading = digits[:-3]
            groups = []
            while leading:
                groups.insert(0, leading[-2:])
                leading = leading[:-2]
            whole = f"{','.join(groups)},{tail}"
        else:
            whole = digits
    return f"{currency_code} {whole}.{fraction}"


@login_required
def invoice_pdf(request, pk, download=False):
    profile, companies, factories, departments, units = scope(request.user)
    from apps.enterprise.models import PortalAccess
    portal_access = PortalAccess.objects.filter(user=request.user, is_active=True, party_type=PortalAccess.PartyType.CUSTOMER).first()
    invoice_scope = BillingDocument.objects.select_related("factory__company", "customer").prefetch_related("lines__product").filter(kind=BillingDocument.Kind.SALES_INVOICE)
    invoice_scope = invoice_scope.filter(factory__in=factories) | (invoice_scope.filter(customer=portal_access.customer) if portal_access else invoice_scope.none())
    invoice = get_object_or_404(
        invoice_scope,
        pk=pk,
    )
    navy = colors.HexColor("#0B2C50")
    gold = colors.HexColor("#D7A13D")
    ink = colors.HexColor("#172A42")
    muted = colors.HexColor("#61748A")
    line_color = colors.HexColor("#D7DEE7")
    pale = colors.HexColor("#F4F7FA")
    gold_pale = colors.HexColor("#FFF3D6")
    page_width, _ = A4
    left_margin = right_margin = 13 * mm
    content_width = page_width - left_margin - right_margin
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=right_margin,
        leftMargin=left_margin,
        topMargin=21 * mm,
        bottomMargin=18 * mm,
        title=f"Invoice {invoice.number}",
        author=invoice.factory.company.name,
    )
    base = getSampleStyleSheet()
    styles = {
        "company": ParagraphStyle(
            "InvoiceCompany", parent=base["Heading1"], fontName="Helvetica-Bold",
            fontSize=17, leading=20, textColor=navy, spaceAfter=2,
        ),
        "small": ParagraphStyle(
            "InvoiceSmall", parent=base["BodyText"], fontName="Helvetica",
            fontSize=7.5, leading=10, textColor=muted,
        ),
        "small_white": ParagraphStyle(
            "InvoiceSmallWhite", parent=base["BodyText"], fontName="Helvetica",
            fontSize=7.5, leading=10, textColor=colors.white,
        ),
        "invoice_title": ParagraphStyle(
            "InvoiceTitle", parent=base["Heading1"], fontName="Helvetica-Bold",
            fontSize=22, leading=25, textColor=colors.white, alignment=TA_RIGHT,
        ),
        "section": ParagraphStyle(
            "InvoiceSection", parent=base["Heading3"], fontName="Helvetica-Bold",
            fontSize=10, leading=13, textColor=colors.white,
        ),
        "customer": ParagraphStyle(
            "InvoiceCustomer", parent=base["BodyText"], fontName="Helvetica",
            fontSize=8.5, leading=12, textColor=ink,
        ),
        "customer_name": ParagraphStyle(
            "InvoiceCustomerName", parent=base["BodyText"], fontName="Helvetica-Bold",
            fontSize=10, leading=13, textColor=navy,
        ),
        "table_header": ParagraphStyle(
            "InvoiceTableHeader", parent=base["BodyText"], fontName="Helvetica-Bold",
            fontSize=7.5, leading=9, textColor=colors.white,
        ),
        "table_body": ParagraphStyle(
            "InvoiceTableBody", parent=base["BodyText"], fontName="Helvetica",
            fontSize=7.5, leading=10, textColor=ink,
        ),
        "table_body_bold": ParagraphStyle(
            "InvoiceTableBodyBold", parent=base["BodyText"], fontName="Helvetica-Bold",
            fontSize=7.5, leading=10, textColor=navy,
        ),
        "amount_words": ParagraphStyle(
            "InvoiceAmountWords", parent=base["BodyText"], fontName="Helvetica",
            fontSize=9, leading=13, textColor=ink,
        ),
        "total": ParagraphStyle(
            "InvoiceTotal", parent=base["BodyText"], fontName="Helvetica-Bold",
            fontSize=10, leading=13, textColor=navy,
        ),
        "right": ParagraphStyle(
            "InvoiceRight", parent=base["BodyText"], fontName="Helvetica",
            fontSize=8, leading=11, textColor=ink, alignment=TA_RIGHT,
        ),
    }

    company = invoice.factory.company
    factory = invoice.factory
    company_address = ", ".join(part for part in (
        factory.address or company.address,
        factory.city or company.city,
        factory.state or company.state,
        company.country,
    ) if part)
    company_contacts = "  |  ".join(value for value in (company.phone, company.email) if value)
    company_details = [
        Paragraph(_pdf_text(company.name), styles["company"]),
        Paragraph(_pdf_text(factory.name), styles["small"]),
    ]
    if company_address:
        company_details.append(Paragraph(_pdf_text(company_address), styles["small"]))
    if company_contacts:
        company_details.append(Paragraph(_pdf_text(company_contacts), styles["small"]))
    company_gstin = invoice.supplier_gstin or factory.gstin
    if company_gstin:
        company_details.append(Paragraph(f"<b>GSTIN:</b> {_pdf_text(company_gstin)}", styles["small"]))

    document_heading = {
        BillingDocument.Status.DRAFT: "DRAFT - NOT A TAX INVOICE",
        BillingDocument.Status.APPROVED: "APPROVED - NOT ISSUED",
        BillingDocument.Status.VOID: "CANCELLED - VOID",
    }.get(invoice.status, "TAX INVOICE")
    invoice_meta = [
        Paragraph(_pdf_text(document_heading), styles["invoice_title"]),
        Spacer(1, 2 * mm),
        Paragraph(f"<b>Invoice No.</b> &nbsp; {_pdf_text(invoice.number)}", styles["small_white"]),
        Paragraph(f"<b>Date</b> &nbsp; {_pdf_text(invoice.issue_date.strftime('%d %b %Y'))}", styles["small_white"]),
        Paragraph(f"<b>Due date</b> &nbsp; {_pdf_text(invoice.due_date.strftime('%d %b %Y') if invoice.due_date else 'Not set')}", styles["small_white"]),
        Paragraph(f"<b>Status</b> &nbsp; {_pdf_text(invoice.get_status_display())}", styles["small_white"]),
    ]
    header = Table(
        [[company_details, invoice_meta]],
        colWidths=[content_width * 0.59, content_width * 0.41],
    )
    header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), colors.white),
        ("BACKGROUND", (1, 0), (1, 0), navy),
        ("BOX", (0, 0), (-1, -1), 0.7, gold),
        ("LINEBEFORE", (1, 0), (1, -1), 2, gold),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 11),
        ("RIGHTPADDING", (0, 0), (-1, -1), 11),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
    ]))

    customer = invoice.customer
    bill_address = (customer.billing_address or customer.address) if customer else ""
    ship_address = (customer.shipping_address or customer.address) if customer else ""
    customer_gstin = invoice.customer_gstin or (
        customer.tax_registration_number if customer else ""
    )

    def address_card(title, address):
        details = [
            Paragraph(
                _pdf_text(customer.name if customer else "Customer details unavailable"),
                styles["customer_name"],
            )
        ]
        if address:
            details.append(Paragraph(_pdf_text(address).replace("\n", "<br/>"), styles["customer"]))
        if customer and customer.email:
            details.append(Paragraph(_pdf_text(customer.email), styles["customer"]))
        if customer and customer.phone:
            details.append(Paragraph(_pdf_text(customer.phone), styles["customer"]))
        if customer_gstin:
            details.append(Paragraph(f"<b>GSTIN:</b> {_pdf_text(customer_gstin)}", styles["customer"]))
        card = Table(
            [[Paragraph(title, styles["section"])], [details]],
            colWidths=[content_width / 2 - 3 * mm],
        )
        card.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), navy),
            ("BACKGROUND", (0, 1), (-1, 1), pale),
            ("BOX", (0, 0), (-1, -1), 0.65, gold),
            ("LINEBELOW", (0, 0), (-1, 0), 1, gold),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 9),
            ("RIGHTPADDING", (0, 0), (-1, -1), 9),
            ("TOPPADDING", (0, 0), (-1, 0), 6),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
            ("TOPPADDING", (0, 1), (-1, -1), 9),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 9),
        ]))
        return card

    half_width = (content_width - 5 * mm) / 2
    addresses = Table(
        [[address_card("BILL TO", bill_address), address_card("SHIP TO", ship_address)]],
        colWidths=[half_width, half_width],
    )
    addresses.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (0, -1), 2.5 * mm),
        ("RIGHTPADDING", (1, 0), (1, -1), 0),
        ("LEFTPADDING", (1, 0), (1, -1), 2.5 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))

    rows = [[
        Paragraph("#", styles["table_header"]),
        Paragraph("Description", styles["table_header"]),
        Paragraph("HSN/SAC", styles["table_header"]),
        Paragraph("Qty", styles["table_header"]),
        Paragraph(f"Unit price ({_pdf_text(invoice.currency_code)})", styles["table_header"]),
        Paragraph(f"Amount ({_pdf_text(invoice.currency_code)})", styles["table_header"]),
    ]]
    for index, item in enumerate(invoice.lines.all(), start=1):
        rows.append([
            str(index),
            Paragraph(_pdf_text(item.description).replace("\n", "<br/>"), styles["table_body"]),
            _pdf_text(item.hsn_sac_code or "—"),
            f"{item.quantity:,.3f}".rstrip("0").rstrip("."),
            f"{item.unit_price:,.2f}",
            f"{item.line_total:,.2f}",
        ])
    item_table = Table(
        rows,
        colWidths=[9 * mm, 64 * mm, 22 * mm, 15 * mm, 34 * mm, content_width - 144 * mm],
        repeatRows=1,
        hAlign="LEFT",
    )
    item_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), navy),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, line_color),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, pale]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("BOX", (0, 0), (-1, -1), 0.7, gold),
    ]))

    def money(value):
        return _format_invoice_money(value, invoice.currency_code)

    summary_rows = [
        [Paragraph("Subtotal", styles["table_body_bold"]), Paragraph(money(invoice.subtotal), styles["right"])],
    ]
    if invoice.discount_amount:
        summary_rows.append([
            Paragraph("Discount", styles["table_body"]),
            Paragraph(f"- {money(invoice.discount_amount)}", styles["right"]),
        ])
    summary_rows.append([
        Paragraph("Taxable value", styles["table_body_bold"]),
        Paragraph(money(invoice.taxable_value), styles["right"]),
    ])
    if invoice.cgst_amount:
        summary_rows.append([
            Paragraph("CGST", styles["table_body"]),
            Paragraph(money(invoice.cgst_amount), styles["right"]),
        ])
    if invoice.sgst_amount:
        summary_rows.append([
            Paragraph("SGST", styles["table_body"]),
            Paragraph(money(invoice.sgst_amount), styles["right"]),
        ])
    if invoice.igst_amount:
        summary_rows.append([
            Paragraph("IGST", styles["table_body"]),
            Paragraph(money(invoice.igst_amount), styles["right"]),
        ])
    summary_rows.append([
        Paragraph("TOTAL AMOUNT DUE", styles["total"]),
        Paragraph(money(invoice.total), styles["total"]),
    ])
    summary = Table(summary_rows, colWidths=[42 * mm, 43 * mm], hAlign="RIGHT")
    summary.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -2), 0.35, line_color),
        ("BACKGROUND", (0, 0), (-1, -2), pale),
        ("BACKGROUND", (0, -1), (-1, -1), gold_pale),
        ("BOX", (0, 0), (-1, -1), 0.7, gold),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))

    amount_words = Table(
        [[
            Paragraph("<b>AMOUNT IN WORDS</b>", styles["table_body_bold"]),
            Paragraph(_pdf_text(_amount_in_words(invoice.total, invoice.currency_code)), styles["amount_words"]),
        ]],
        colWidths=[38 * mm, content_width - 38 * mm],
    )
    amount_words.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), pale),
        ("BOX", (0, 0), (-1, -1), 0.6, line_color),
        ("LINEBEFORE", (0, 0), (0, -1), 3, gold),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))

    payment_details = [
        Paragraph("<b>PAYMENT &amp; TERMS</b>", styles["table_body_bold"]),
        Paragraph(f"Terms: {_pdf_text(invoice.payment_terms or 'As agreed')}", styles["small"]),
        Paragraph(f"Due date: {_pdf_text(invoice.due_date.strftime('%d %b %Y') if invoice.due_date else 'Not set')}", styles["small"]),
        Paragraph(f"Place of supply: {_pdf_text(invoice.place_of_supply_state_code or '—')} ({_pdf_text(invoice.gst_supply_type or 'unclassified')})", styles["small"]),
    ]
    if invoice.notes:
        payment_details.append(Paragraph(_pdf_text(invoice.notes), styles["small"]))
    terms = Table(
        [[payment_details, [
            Paragraph(_pdf_text(company.name), styles["table_body_bold"]),
            Spacer(1, 15 * mm),
            Paragraph("Authorized signatory", styles["small"]),
        ]]],
        colWidths=[half_width, half_width],
    )
    terms.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.65, gold),
        ("LINEBEFORE", (1, 0), (1, -1), 0.4, line_color),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))

    story = [
        header,
        Spacer(1, 5 * mm),
        addresses,
        Spacer(1, 5 * mm),
        item_table,
        Spacer(1, 3 * mm),
        summary,
        Spacer(1, 3 * mm),
        amount_words,
        Spacer(1, 4 * mm),
        terms,
    ]
    if invoice.cancellation_reason:
        story.extend([
            Spacer(1, 3 * mm),
            Paragraph(f"<b>Cancellation reason:</b> {_pdf_text(invoice.cancellation_reason)}", styles["small"]),
        ])
    document.build(
        story,
        onFirstPage=lambda canvas, doc: premium_page_chrome(
            canvas, doc, "Tax invoice", invoice.factory.company.name
        ),
        onLaterPages=lambda canvas, doc: premium_page_chrome(
            canvas, doc, "Tax invoice", invoice.factory.company.name
        ),
    )
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    filename = re.sub(r"[^A-Za-z0-9._-]", "_", invoice.number)
    disposition = "attachment" if download else "inline"
    response["Content-Disposition"] = f'{disposition}; filename="{filename}.pdf"'
    return response


@login_required
def invoice_pdf_download(request, pk):
    return invoice_pdf(request, pk, download=True)


def _gst_period(request):
    period = request.GET.get("period", timezone.localdate().strftime("%Y-%m"))
    if not re.fullmatch(r"\d{4}-\d{2}", period):
        raise Http404("Use a tax period in YYYY-MM format.")
    try:
        year, month = (int(part) for part in period.split("-", 1))
        start = date(year, month, 1)
        end = date(year, month, calendar.monthrange(year, month)[1])
    except (ValueError, TypeError):
        raise Http404("Use a tax period in YYYY-MM format.")
    return period, start, end


def _csv_text(value):
    value = str(value or "")
    return f"'{value}" if value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")) else value


def _gst_report_rows(invoices, adjustments=()):
    summary = {}
    totals = {}
    for invoice in invoices:
        currency_totals = totals.setdefault(invoice.currency_code, {
            "currency_code": invoice.currency_code,
            "taxable_value": Decimal("0.00"),
            "cgst_amount": Decimal("0.00"),
            "sgst_amount": Decimal("0.00"),
            "igst_amount": Decimal("0.00"),
            "tax_amount": Decimal("0.00"),
        })
        lines = list(invoice.lines.all())
        has_line_snapshot = any(
            line.taxable_value or line.tax_rate or line.cgst_amount or line.sgst_amount or line.igst_amount
            for line in lines
        )
        if not invoice.gst_supply_type and not has_line_snapshot:
            row = summary.setdefault((
                invoice.currency_code,
                "legacy/unclassified",
                invoice.place_of_supply_state_code,
                "unclassified",
                invoice.tax_rate,
            ), {
                "currency_code": invoice.currency_code,
                "supply_type": "legacy/unclassified",
                "place_of_supply": invoice.place_of_supply_state_code or "—",
                "hsn_sac_code": "unclassified",
                "tax_rate": invoice.tax_rate,
                "taxable_value": Decimal("0.00"),
                "cgst_amount": Decimal("0.00"),
                "sgst_amount": Decimal("0.00"),
                "igst_amount": Decimal("0.00"),
                "tax_amount": Decimal("0.00"),
            })
            row["taxable_value"] += invoice.taxable_value
            row["tax_amount"] += invoice.tax_amount
            currency_totals["taxable_value"] += invoice.taxable_value
            currency_totals["tax_amount"] += invoice.tax_amount
            continue
        for line in lines:
            key = (
                invoice.currency_code,
                invoice.gst_supply_type,
                invoice.place_of_supply_state_code,
                line.hsn_sac_code,
                line.tax_rate,
            )
            row = summary.setdefault(key, {
                "currency_code": invoice.currency_code,
                "supply_type": invoice.gst_supply_type or "unclassified",
                "place_of_supply": invoice.place_of_supply_state_code or "—",
                "hsn_sac_code": line.hsn_sac_code or "—",
                "tax_rate": line.tax_rate,
                "taxable_value": Decimal("0.00"),
                "cgst_amount": Decimal("0.00"),
                "sgst_amount": Decimal("0.00"),
                "igst_amount": Decimal("0.00"),
                "tax_amount": Decimal("0.00"),
            })
            row["taxable_value"] += line.taxable_value
            row["cgst_amount"] += line.cgst_amount
            row["sgst_amount"] += line.sgst_amount
            row["igst_amount"] += line.igst_amount
            line_tax = line.cgst_amount + line.sgst_amount + line.igst_amount
            row["tax_amount"] += line_tax
            currency_totals["taxable_value"] += line.taxable_value
            currency_totals["cgst_amount"] += line.cgst_amount
            currency_totals["sgst_amount"] += line.sgst_amount
            currency_totals["igst_amount"] += line.igst_amount
            currency_totals["tax_amount"] += line_tax
    for adjustment in adjustments:
        invoice = adjustment.source_document
        currency_totals = totals.setdefault(invoice.currency_code, {
            "currency_code": invoice.currency_code,
            "taxable_value": Decimal("0.00"),
            "cgst_amount": Decimal("0.00"),
            "sgst_amount": Decimal("0.00"),
            "igst_amount": Decimal("0.00"),
            "tax_amount": Decimal("0.00"),
        })
        for line in adjustment.lines.all():
            key = (
                invoice.currency_code,
                invoice.gst_supply_type or "unclassified",
                invoice.place_of_supply_state_code,
                line.hsn_sac_code,
                line.tax_rate,
            )
            row = summary.setdefault(key, {
                "currency_code": invoice.currency_code,
                "supply_type": invoice.gst_supply_type or "unclassified",
                "place_of_supply": invoice.place_of_supply_state_code or "—",
                "hsn_sac_code": line.hsn_sac_code or "—",
                "tax_rate": line.tax_rate,
                "taxable_value": Decimal("0.00"),
                "cgst_amount": Decimal("0.00"),
                "sgst_amount": Decimal("0.00"),
                "igst_amount": Decimal("0.00"),
                "tax_amount": Decimal("0.00"),
            })
            tax_amount = line.cgst_amount + line.sgst_amount + line.igst_amount
            row["taxable_value"] -= line.taxable_value
            row["cgst_amount"] -= line.cgst_amount
            row["sgst_amount"] -= line.sgst_amount
            row["igst_amount"] -= line.igst_amount
            row["tax_amount"] -= tax_amount
            currency_totals["taxable_value"] -= line.taxable_value
            currency_totals["cgst_amount"] -= line.cgst_amount
            currency_totals["sgst_amount"] -= line.sgst_amount
            currency_totals["igst_amount"] -= line.igst_amount
            currency_totals["tax_amount"] -= tax_amount
    return (
        sorted(summary.values(), key=lambda row: (row["currency_code"], row["supply_type"], row["place_of_supply"], row["hsn_sac_code"], row["tax_rate"])),
        sorted(totals.values(), key=lambda row: row["currency_code"]),
    )


@login_required
def gst_report(request):
    profile, companies, factories, departments, units = scope(request.user)
    period, start, end = _gst_period(request)
    invoices = list(
        BillingDocument.objects.filter(
            factory__in=factories,
            kind=BillingDocument.Kind.SALES_INVOICE,
            status__in=[
                BillingDocument.Status.ISSUED,
                BillingDocument.Status.PART_PAID,
                BillingDocument.Status.PAID,
            ],
            issue_date__range=(start, end),
        )
        .select_related("factory", "customer")
        .prefetch_related("lines")
        .order_by("issue_date", "number")
    )
    adjustments = list(
        CreditDebitNote.objects.filter(
            kind=CreditDebitNote.Kind.CREDIT_NOTE,
            status=CreditDebitNote.Status.APPROVED,
            issue_date__range=(start, end),
            source_document__factory__in=factories,
            source_document__kind=BillingDocument.Kind.SALES_INVOICE,
            source_document__status__in=[
                BillingDocument.Status.ISSUED,
                BillingDocument.Status.PART_PAID,
                BillingDocument.Status.PAID,
            ],
        )
        .select_related("source_document")
        .prefetch_related("lines")
        .order_by("issue_date", "number")
    )
    summary, totals = _gst_report_rows(invoices, adjustments)
    unclassified_invoice_count = sum(
        not invoice.gst_supply_type
        and not any(line.taxable_value or line.tax_rate or line.cgst_amount or line.sgst_amount or line.igst_amount for line in invoice.lines.all())
        for invoice in invoices
    )
    return render_page(request, "billing/gst_report.html", {
        "period": period,
        "summary": summary,
        "invoice_count": len(invoices),
        "currency_totals": totals,
        "unclassified_invoice_count": unclassified_invoice_count,
    })


@login_required
def gst_reconciliation_export(request):
    profile, companies, factories, departments, units = scope(request.user)
    period, start, end = _gst_period(request)
    invoices = list(
        BillingDocument.objects.filter(
            factory__in=factories,
            kind=BillingDocument.Kind.SALES_INVOICE,
            status__in=[
                BillingDocument.Status.ISSUED,
                BillingDocument.Status.PART_PAID,
                BillingDocument.Status.PAID,
            ],
            issue_date__range=(start, end),
        )
        .select_related("factory", "customer")
        .prefetch_related("lines")
        .order_by("issue_date", "number")
    )
    adjustments = CreditDebitNote.objects.filter(
        kind=CreditDebitNote.Kind.CREDIT_NOTE,
        status=CreditDebitNote.Status.APPROVED,
        issue_date__range=(start, end),
        source_document__factory__in=factories,
        source_document__kind=BillingDocument.Kind.SALES_INVOICE,
        source_document__status__in=[
            BillingDocument.Status.ISSUED,
            BillingDocument.Status.PART_PAID,
            BillingDocument.Status.PAID,
        ],
    ).select_related(
        "source_document__factory",
        "source_document__customer",
    ).prefetch_related("lines").order_by("issue_date", "number")
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="gst-reconciliation-{period}.csv"'
    writer = csv.writer(response)
    writer.writerow([
        "Period", "Invoice number", "Invoice date", "Currency", "Supplier GSTIN", "Customer",
        "Customer GSTIN", "Place of supply", "Supply type", "HSN/SAC", "GST rate",
        "Description", "Gross amount", "Discount", "Taxable value", "CGST", "SGST", "IGST", "Line total",
    ])
    for invoice in invoices:
        lines = list(invoice.lines.all())
        if not invoice.gst_supply_type and not any(
            line.taxable_value or line.tax_rate or line.cgst_amount or line.sgst_amount or line.igst_amount
            for line in lines
        ):
            writer.writerow([
                period, _csv_text(invoice.number), invoice.issue_date.isoformat(), invoice.currency_code,
                _csv_text(invoice.supplier_gstin), _csv_text(invoice.customer.name), _csv_text(invoice.customer_gstin),
                _csv_text(invoice.place_of_supply_state_code), "legacy/unclassified", "unclassified",
                invoice.tax_rate, "Legacy invoice totals; tax components unavailable",
                invoice.subtotal, invoice.discount_amount, invoice.taxable_value,
                "", "", "", invoice.total,
            ])
            continue
        for line in lines:
            writer.writerow([
                period,
                _csv_text(invoice.number),
                invoice.issue_date.isoformat(),
                invoice.currency_code,
                _csv_text(invoice.supplier_gstin),
                _csv_text(invoice.customer.name),
                _csv_text(invoice.customer_gstin),
                _csv_text(invoice.place_of_supply_state_code),
                _csv_text(invoice.gst_supply_type),
                _csv_text(line.hsn_sac_code),
                line.tax_rate,
                _csv_text(line.description),
                line.line_total,
                line.discount_amount,
                line.taxable_value,
                line.cgst_amount,
                line.sgst_amount,
                line.igst_amount,
                line.taxable_value + line.cgst_amount + line.sgst_amount + line.igst_amount,
            ])
    for adjustment in adjustments:
        invoice = adjustment.source_document
        for line in adjustment.lines.all():
            writer.writerow([
                period,
                _csv_text(adjustment.number),
                adjustment.issue_date.isoformat(),
                adjustment.currency_code,
                _csv_text(invoice.supplier_gstin),
                _csv_text(invoice.customer.name),
                _csv_text(invoice.customer_gstin),
                _csv_text(invoice.place_of_supply_state_code),
                _csv_text(invoice.gst_supply_type),
                _csv_text(line.hsn_sac_code),
                line.tax_rate,
                _csv_text(f"Credit note: {line.description}"),
                -line.line_total,
                -line.discount_amount,
                -line.taxable_value,
                -line.cgst_amount,
                -line.sgst_amount,
                -line.igst_amount,
                -line.total,
            ])
    return response
