import json
from tempfile import TemporaryDirectory
from datetime import timedelta
from decimal import Decimal

from django.contrib.messages import get_messages
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import UserActivity, UserProfile
from apps.operations.models import BillingDocument, BillingLine, BillingPayment, BillingRefund, CreditDebitNote, Customer, GSTTaxProfile, GSTTaxRate, InventoryItem, ProcurementDocument, Product, ProductCategory, StockMovement, Supplier, Warehouse
from apps.billing.validators import validate_gstin, validate_hsn_sac
from apps.billing.views import _amount_in_words, _format_invoice_money
from apps.organization.models import Company, Factory


class SalesInvoiceWorkflowTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            username="billing-admin",
            email="billing@example.com",
            password="test-password",
        )
        self.client.force_login(self.user)
        self.company = Company.objects.create(
            name="Example Company",
            address="100 Industrial Road",
            city="Mumbai",
            state="Maharashtra",
        )
        self.factory = Factory.objects.create(
            company=self.company,
            name="Main Factory",
            factory_code="MAIN",
            address="100 Industrial Road",
            city="Mumbai",
            state="Maharashtra",
            gstin="27AAPFU0939F1ZV",
            state_code="27",
        )
        self.customer = Customer.objects.create(
            company=self.company,
            name="Example Customer",
            address="200 Customer Avenue",
            state="Maharashtra",
            state_code="27",
        )
        self.category = ProductCategory.objects.create(name="Services")
        self.product = Product.objects.create(
            sku="SVC-01",
            name="Consulting",
            category=self.category,
            selling_price=Decimal("100.00"),
            tax_rate=Decimal("18.000"),
            hsn_sac_code="998311",
        )

    def create_invoice(self, description="Consulting package", discount_amount="10"):
        return self.client.post(
            reverse("billing:document_create"),
            {
                "factory": self.factory.pk,
                "customer": self.customer.pk,
                "issue_date": "2026-11-05",
                "due_date": "2026-12-05",
                "payment_terms": "Net 30",
                "tax_rate": "18",
                "discount_amount": discount_amount,
                "currency_code": "INR",
                "notes": "Thank you for your business.",
                "lines": json.dumps([
                    {
                        "description": description,
                        "product": str(self.product.pk),
                        "quantity": "2.5",
                        "unit_price": "100.00",
                    },
                ]),
            },
        )

    def test_create_saves_a_draft_with_calculated_totals_and_unique_number(self):
        billing_page = self.client.get(reverse("billing:home"))
        self.assertEqual(billing_page.status_code, 200)
        self.assertContains(billing_page, "Create a customer invoice")
        self.assertContains(billing_page, "Add customer")
        self.assertContains(
            billing_page,
            f'href="{reverse("billing:customer_create")}"',
        )
        self.assertContains(
            billing_page,
            f'href="{reverse("billing:document_create")}"',
        )

        create_page = self.client.get(reverse("billing:document_create"))
        self.assertEqual(create_page.status_code, 200)
        self.assertContains(create_page, "Create sales invoice")
        self.assertContains(create_page, "Confirm its GSTIN")
        self.assertContains(create_page, "Customer missing?")
        self.assertContains(
            create_page,
            f'href="{reverse("billing:customer_create")}"',
        )
        self.assertContains(create_page, "id_customer")
        self.assertContains(create_page, "HSN/SAC")
        self.assertContains(create_page, "CGST")

        response = self.create_invoice()
        self.assertEqual(response.status_code, 302)
        invoice = BillingDocument.objects.get()
        self.assertEqual(invoice.number, "INV-MAIN-26-00001")
        self.assertEqual(invoice.kind, BillingDocument.Kind.SALES_INVOICE)
        self.assertEqual(invoice.status, BillingDocument.Status.DRAFT)
        self.assertEqual(invoice.subtotal, Decimal("250.00"))
        self.assertEqual(invoice.taxable_value, Decimal("240.00"))
        self.assertEqual(invoice.tax_amount, Decimal("43.20"))
        self.assertEqual(invoice.cgst_amount, Decimal("21.60"))
        self.assertEqual(invoice.sgst_amount, Decimal("21.60"))
        self.assertEqual(invoice.igst_amount, Decimal("0.00"))
        self.assertEqual(invoice.total, Decimal("283.20"))
        self.assertEqual(invoice.payment_terms, "Net 30")
        self.assertEqual(invoice.lines.get().product, self.product)
        self.assertEqual(invoice.lines.get().hsn_sac_code, "998311")
        detail = self.client.get(reverse("billing:document_detail", args=[invoice.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Approve invoice")
        self.assertContains(detail, "Cancel invoice")

        self.create_invoice("Second package")
        self.assertEqual(
            list(BillingDocument.objects.order_by("number").values_list("number", flat=True)),
            ["INV-MAIN-26-00001", "INV-MAIN-26-00002"],
        )

    def test_customer_registration_requires_actual_tax_and_address_confirmation(self):
        create_url = reverse("billing:customer_create")
        page = self.client.get(create_url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Use verified records only")
        self.assertContains(page, "GSTIN format checks do not confirm")
        payload = {
            "company": self.company.pk,
            "customer_type": Customer.CustomerType.BUSINESS,
            "name": "Verified Buyer Ltd",
            "email": "buyer@example.com",
            "phone": "9876543210",
            "address": "200 Customer Avenue, Mumbai, Maharashtra",
            "tax_registration_number": "",
            "state": "Maharashtra",
            "state_code": "27",
            "billing_address": "",
            "shipping_address": "",
            "credit_limit": "0.00",
            "is_active": "on",
        }

        rejected = self.client.post(create_url, payload)

        self.assertEqual(rejected.status_code, 200)
        self.assertFalse(Customer.objects.filter(name="Verified Buyer Ltd").exists())
        self.assertContains(rejected, "Confirm the customer information")

        payload["customer_details_confirmed"] = "on"
        accepted = self.client.post(create_url, payload)

        self.assertRedirects(accepted, reverse("operations:billing"), fetch_redirect_response=False)
        customer = Customer.objects.get(name="Verified Buyer Ltd")
        self.assertEqual(customer.state_code, "27")
        event = UserActivity.objects.get(action="billing_customer_created")
        self.assertTrue(event.details["official_records_confirmed"])
        self.assertEqual(event.details["state_code"], "27")

    def test_customer_registration_rejects_mismatched_gstin_state(self):
        response = self.client.post(
            reverse("billing:customer_create"),
            {
                "company": self.company.pk,
                "customer_type": Customer.CustomerType.BUSINESS,
                "name": "Mismatch Buyer Ltd",
                "address": "200 Customer Avenue, Mumbai, Maharashtra",
                "tax_registration_number": "29AAPFU0939F1ZR",
                "state": "Maharashtra",
                "state_code": "27",
                "credit_limit": "0",
                "is_active": "on",
                "customer_details_confirmed": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Customer.objects.filter(name="Mismatch Buyer Ltd").exists())
        self.assertContains(response, "GSTIN state code must match")

    def test_invoice_requires_approval_before_issue_and_records_actors(self):
        self.create_invoice()
        invoice = BillingDocument.objects.get()

        response = self.client.post(reverse("billing:invoice_issue", args=[invoice.pk]))
        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, BillingDocument.Status.DRAFT)

        self.client.post(reverse("billing:invoice_approve", args=[invoice.pk]))
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, BillingDocument.Status.APPROVED)
        self.assertEqual(invoice.approved_by, self.user)
        self.assertIsNotNone(invoice.approved_at)

        self.client.post(
            reverse("billing:invoice_issue", args=[invoice.pk]),
            {"confirm_legal_accuracy": "on"},
        )
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, BillingDocument.Status.ISSUED)
        self.assertEqual(invoice.issued_by, self.user)
        self.assertIsNotNone(invoice.issued_at)
        self.assertTrue(UserActivity.objects.filter(action="sales_invoice_approved").exists())
        self.assertTrue(UserActivity.objects.filter(action="sales_invoice_issued").exists())
        issued_event = UserActivity.objects.get(action="sales_invoice_issued")
        self.assertTrue(issued_event.details["legal_invoice_details_confirmed"])
        self.assertEqual(issued_event.details["customer_gstin"], "")

    def test_issue_requires_explicit_legal_detail_confirmation(self):
        self.create_invoice()
        invoice = BillingDocument.objects.get()
        invoice.status = BillingDocument.Status.APPROVED
        invoice.save(update_fields=["status"])

        response = self.client.post(reverse("billing:invoice_issue", args=[invoice.pk]))

        invoice.refresh_from_db()
        self.assertEqual(invoice.status, BillingDocument.Status.APPROVED)
        self.assertTrue(any("Confirm that the invoice" in str(message) for message in get_messages(response.wsgi_request)))

    def test_issue_blocks_missing_customer_address_even_when_invoice_is_non_taxable(self):
        self.product.tax_rate = Decimal("0")
        self.product.save(update_fields=["tax_rate"])
        self.create_invoice()
        invoice = BillingDocument.objects.get()
        invoice.status = BillingDocument.Status.APPROVED
        invoice.save(update_fields=["status"])
        self.customer.address = ""
        self.customer.save(update_fields=["address"])

        response = self.client.post(
            reverse("billing:invoice_issue", args=[invoice.pk]),
            {"confirm_legal_accuracy": "on"},
        )

        invoice.refresh_from_db()
        self.assertEqual(invoice.status, BillingDocument.Status.APPROVED)
        self.assertTrue(any("customer's billing or registered address" in str(message) for message in get_messages(response.wsgi_request)))

    def test_discount_cannot_exceed_subtotal(self):
        response = self.create_invoice(discount_amount="251")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(BillingDocument.objects.exists())
        self.assertContains(response, "Discount cannot be negative or exceed the invoice subtotal.")

    def test_cancellation_requires_reason_and_keeps_audit_details(self):
        self.create_invoice()
        invoice = BillingDocument.objects.get()

        self.client.post(reverse("billing:invoice_cancel", args=[invoice.pk]), {"reason": "   "})
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, BillingDocument.Status.DRAFT)

        self.client.post(
            reverse("billing:invoice_cancel", args=[invoice.pk]),
            {"reason": "Created for the wrong customer"},
        )
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, BillingDocument.Status.VOID)
        self.assertEqual(invoice.canceled_by, self.user)
        self.assertIsNotNone(invoice.canceled_at)
        self.assertEqual(invoice.cancellation_reason, "Created for the wrong customer")
        audit_event = UserActivity.objects.get(action="sales_invoice_cancelled")
        self.assertEqual(audit_event.details["cancellation_reason"], "Created for the wrong customer")
        self.assertEqual(audit_event.details["from_status"], BillingDocument.Status.DRAFT)
        self.assertEqual(audit_event.details["to_status"], BillingDocument.Status.VOID)

    def test_invoice_pdf_print_and_download(self):
        self.create_invoice()
        invoice = BillingDocument.objects.get()

        printed = self.client.get(reverse("billing:invoice_pdf", args=[invoice.pk]))
        self.assertEqual(printed.status_code, 200)
        self.assertEqual(printed["Content-Type"], "application/pdf")
        self.assertIn("inline", printed["Content-Disposition"])
        self.assertTrue(printed.content.startswith(b"%PDF"))

        downloaded = self.client.get(reverse("billing:invoice_pdf_download", args=[invoice.pk]))
        self.assertEqual(downloaded.status_code, 200)
        self.assertIn("attachment", downloaded["Content-Disposition"])
        self.assertIn(b"/Type /Page", printed.content)

    def test_invoice_amount_in_words_uses_indian_number_groups(self):
        self.assertEqual(
            _amount_in_words(Decimal("442500.00"), "INR"),
            "Rupees Four Lakh Forty Two Thousand Five Hundred Only",
        )
        self.assertEqual(
            _amount_in_words(Decimal("283.20"), "INR"),
            "Rupees Two Hundred Eighty Three and Paise Twenty Only",
        )
        self.assertEqual(
            _format_invoice_money(Decimal("442500.00"), "INR"),
            "INR 4,42,500.00",
        )
        self.assertEqual(
            _format_invoice_money(Decimal("442500.00"), "USD"),
            "USD 442,500.00",
        )

    def test_product_profiles_apply_individual_effective_rates_and_interstate_igst(self):
        profile = GSTTaxProfile.objects.create(name="Reduced GST", code="REDUCED")
        GSTTaxRate.objects.create(
            profile=profile,
            rate=Decimal("5.000"),
            effective_from="2026-01-01",
        )
        reduced_product = Product.objects.create(
            sku="SVC-LOW",
            name="Low rate service",
            category=self.category,
            selling_price=Decimal("100.00"),
            gst_tax_profile=profile,
            hsn_sac_code="998312",
        )
        self.customer.state_code = "29"
        self.customer.save(update_fields=["state_code"])
        response = self.client.post(
            reverse("billing:document_create"),
            {
                "factory": self.factory.pk,
                "customer": self.customer.pk,
                "issue_date": "2026-11-05",
                "due_date": "2026-12-05",
                "payment_terms": "Net 30",
                "discount_amount": "0",
                "currency_code": "INR",
                "lines": json.dumps([
                    {"description": "Standard", "product": str(self.product.pk), "quantity": "1", "unit_price": "100"},
                    {"description": "Reduced", "product": str(reduced_product.pk), "quantity": "1", "unit_price": "100"},
                ]),
            },
        )
        self.assertEqual(response.status_code, 302)
        invoice = BillingDocument.objects.get()
        self.assertEqual(invoice.gst_supply_type, "interstate")
        self.assertEqual(invoice.place_of_supply_state_code, "29")
        self.assertEqual(invoice.igst_amount, Decimal("23.00"))
        self.assertEqual(invoice.cgst_amount, Decimal("0.00"))
        self.assertEqual(
            list(invoice.lines.order_by("id").values_list("tax_rate", flat=True)),
            [Decimal("18.000"), Decimal("5.000")],
        )

    def test_gst_period_report_and_reconciliation_export(self):
        self.create_invoice()
        invoice = BillingDocument.objects.get()
        invoice.status = BillingDocument.Status.ISSUED
        invoice.save(update_fields=["status"])
        self.client.post(
            reverse("billing:adjustment_create", args=[invoice.pk]),
            {
                "issue_date": timezone.localdate().isoformat(),
                "reason": "Partial sales return",
                "lines": json.dumps([{
                    "source_line": invoice.lines.get().pk,
                    "quantity": "1",
                }]),
            },
        )
        note = invoice.adjustments.get()
        self.client.post(reverse("billing:adjustment_approve", args=[note.pk]))

        report = self.client.get(reverse("billing:gst_report"), {"period": "2026-11"})
        self.assertEqual(report.status_code, 200)
        self.assertEqual(report.context["invoice_count"], 1)
        self.assertEqual(report.context["currency_totals"][0]["tax_amount"], Decimal("25.92"))

        export = self.client.get(reverse("billing:gst_reconciliation_export"), {"period": "2026-11"})
        self.assertEqual(export.status_code, 200)
        self.assertIn("text/csv", export["Content-Type"])
        self.assertIn(b"Supplier GSTIN", export.content)
        self.assertIn(b"27AAPFU0939F1ZV", export.content)
        self.assertIn(b"Credit note: Consulting package", export.content)
        self.assertIn(b"-8.64", export.content)
        self.assertIn(b"-113.28", export.content)

    def test_issue_requires_valid_gstin_and_hsn_for_taxable_lines(self):
        self.product.hsn_sac_code = ""
        self.product.save(update_fields=["hsn_sac_code"])
        self.create_invoice()
        invoice = BillingDocument.objects.get()
        invoice.status = BillingDocument.Status.APPROVED
        invoice.save(update_fields=["status"])

        response = self.client.post(
            reverse("billing:invoice_issue", args=[invoice.pk]),
            {"confirm_legal_accuracy": "on"},
        )
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, BillingDocument.Status.APPROVED)
        self.assertTrue(any("HSN/SAC" in str(message) for message in get_messages(response.wsgi_request)))

        self.product.hsn_sac_code = "998311"
        self.product.save(update_fields=["hsn_sac_code"])
        self.create_invoice()
        invoice = BillingDocument.objects.order_by("-pk").first()
        invoice.status = BillingDocument.Status.APPROVED
        invoice.save(update_fields=["status"])
        self.factory.gstin = "27ABCDE1234F1ZV"
        self.factory.save(update_fields=["gstin"])
        response = self.client.post(
            reverse("billing:invoice_issue", args=[invoice.pk]),
            {"confirm_legal_accuracy": "on"},
        )
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, BillingDocument.Status.APPROVED)
        self.assertTrue(any("checksum" in str(message) for message in get_messages(response.wsgi_request)))

    def test_gstin_and_hsn_validators_reject_malformed_values(self):
        with self.assertRaises(ValidationError):
            validate_gstin("27ABCDE1234F1Z5")
        with self.assertRaises(ValidationError):
            validate_hsn_sac("12345")

    def test_legacy_operations_invoice_route_uses_line_specific_gst(self):
        response = self.client.post(
            reverse("operations:billing_create"),
            {
                "factory": self.factory.pk,
                "kind": BillingDocument.Kind.SALES_INVOICE,
                "customer": self.customer.pk,
                "issue_date": "2026-11-05",
                "due_date": "2026-12-05",
                "tax_rate": "2",
                "discount_amount": "0",
                "currency_code": "INR",
                "status": BillingDocument.Status.DRAFT,
                "notes": "",
                "lines": json.dumps([
                    {
                        "description": "Configured product",
                        "product": str(self.product.pk),
                        "quantity": "1",
                        "unit_price": "100",
                    },
                ]),
            },
        )
        self.assertEqual(response.status_code, 302)
        invoice = BillingDocument.objects.get()
        self.assertEqual(invoice.tax_amount, Decimal("18.00"))
        self.assertEqual(invoice.cgst_amount, Decimal("9.00"))
        self.assertEqual(invoice.sgst_amount, Decimal("9.00"))


class PaymentManagementTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="payment-admin",
            email="payments@example.com",
            password="test-password",
        )
        self.client.force_login(self.user)
        self.company = Company.objects.create(name="Payment Company")
        self.factory = Factory.objects.create(
            company=self.company,
            name="Payment Factory",
            factory_code="PAY",
            gstin="27AAPFU0939F1ZV",
            state_code="27",
        )
        self.customer = Customer.objects.create(
            company=self.company,
            name="Payment Customer",
            state_code="27",
        )
        today = timezone.localdate()
        self.invoice = BillingDocument.objects.create(
            factory=self.factory,
            number="INV-PAY-00001",
            kind=BillingDocument.Kind.SALES_INVOICE,
            status=BillingDocument.Status.ISSUED,
            customer=self.customer,
            issue_date=today - timedelta(days=30),
            due_date=today - timedelta(days=1),
            total=Decimal("100.00"),
            currency_code="INR",
        )
        self.detail_url = reverse("billing:document_detail", args=[self.invoice.pk])

    def post_payment(self, amount, method="upi", method_details="", **extra):
        data = {
            "action": "record_payment",
            "payment_date": timezone.localdate().isoformat(),
            "amount": str(amount),
            "method": method,
            "method_details": method_details,
            "reference": "BANK-REF-01",
            "notes": "",
        }
        data.update(extra)
        return self.client.post(self.detail_url, data)

    def post_refund(self, payment, amount, reason="Customer return"):
        return self.client.post(
            self.detail_url,
            {
                "action": "record_refund",
                "payment": payment.pk,
                "refund_date": timezone.localdate().isoformat(),
                "amount": str(amount),
                "method": "bank_transfer",
                "method_details": "",
                "reference": "REF-01",
                "reason": reason,
            },
        )

    def test_partial_and_full_payments_generate_receipts_and_update_status(self):
        self.assertEqual(
            self.invoice.payment_status,
            BillingDocument.PaymentStatus.OVERDUE,
        )
        self.assertContains(self.client.get(self.detail_url), "Record payment")
        self.assertContains(self.client.get(self.detail_url), "Overdue")

        self.post_payment("35.00")
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.amount_paid, Decimal("35.00"))
        self.assertEqual(self.invoice.status, BillingDocument.Status.PART_PAID)
        self.assertEqual(self.invoice.payment_status, BillingDocument.PaymentStatus.OVERDUE)

        self.post_payment("65.00", method="cash")
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.amount_paid, Decimal("100.00"))
        self.assertEqual(self.invoice.balance_due, Decimal("0"))
        self.assertEqual(self.invoice.payment_status, BillingDocument.PaymentStatus.PAID)
        self.assertEqual(self.invoice.payments.count(), 2)
        detail = self.client.get(self.detail_url)
        self.assertContains(detail, "Record refund")
        self.assertContains(detail, "Payment history")

        payment = self.invoice.payments.order_by("pk").first()
        receipt = self.client.get(reverse("billing:payment_receipt", args=[payment.pk]))
        self.assertEqual(receipt.status_code, 200)
        self.assertEqual(receipt["Content-Type"], "application/pdf")
        self.assertTrue(receipt.content.startswith(b"%PDF"))
        download = self.client.get(
            reverse("billing:payment_receipt_download", args=[payment.pk])
        )
        self.assertIn("attachment", download["Content-Disposition"])
        self.assertTrue(UserActivity.objects.filter(action="billing_payment_recorded").exists())

    def test_other_payment_method_requires_details_and_payment_cannot_exceed_balance(self):
        response = self.post_payment("10", method="other")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(BillingPayment.objects.exists())
        self.assertContains(response, "Describe the payment method.")

        self.post_payment("101", method="upi")
        self.invoice.refresh_from_db()
        self.assertFalse(BillingPayment.objects.exists())
        self.assertEqual(self.invoice.amount_paid, Decimal("0"))

        self.post_payment("100", method="other", method_details="Mobile wallet")
        payment = BillingPayment.objects.get()
        self.assertEqual(payment.method_details, "Mobile wallet")

    def test_refunds_are_audited_and_cannot_exceed_the_unrefunded_payment(self):
        self.post_payment("80")
        payment = BillingPayment.objects.get()

        self.post_refund(payment, "-1")
        self.assertFalse(BillingRefund.objects.exists())

        self.post_refund(payment, "30")
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.amount_paid, Decimal("50.00"))
        self.assertEqual(self.invoice.status, BillingDocument.Status.PART_PAID)
        self.assertEqual(BillingRefund.objects.get().recorded_by, self.user)

        invalid = self.post_refund(payment, "51")
        self.assertEqual(invalid.status_code, 200)
        self.assertEqual(BillingRefund.objects.count(), 1)

        self.post_refund(payment, "50", reason="Final refund")
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.amount_paid, Decimal("0.00"))
        self.assertEqual(self.invoice.status, BillingDocument.Status.ISSUED)
        self.assertEqual(BillingRefund.objects.count(), 2)
        refund_event = UserActivity.objects.filter(
            action="billing_refund_recorded"
        ).order_by("-created_at").first()
        self.assertEqual(refund_event.details["reason"], "Final refund")
        self.assertEqual(refund_event.details["invoice_number"], self.invoice.number)
        self.assertContains(self.client.get(self.detail_url), "Final refund")

    def test_customer_statement_and_csv_include_payment_and_refund_history(self):
        self.post_payment("80")
        payment = BillingPayment.objects.get()
        self.post_refund(payment, "20")

        statement = self.client.get(
            reverse("billing:customer_statement", args=[self.customer.pk])
        )
        self.assertEqual(statement.status_code, 200)
        self.assertContains(statement, "Customer statement")
        self.assertContains(statement, payment.receipt_number)
        self.assertContains(statement, "Refund")

        export = self.client.get(
            reverse("billing:customer_statement_csv", args=[self.customer.pk])
        )
        self.assertEqual(export.status_code, 200)
        self.assertIn("text/csv", export["Content-Type"])
        self.assertIn(b"Payment", export.content)
        self.assertIn(b"Refund", export.content)

    def test_billing_home_filters_overdue_separately_from_unpaid(self):
        overdue = self.client.get(reverse("billing:home"), {"status": "overdue"})
        self.assertContains(overdue, self.invoice.number)

        self.invoice.due_date = timezone.localdate() + timedelta(days=1)
        self.invoice.save(update_fields=["due_date"])
        unpaid = self.client.get(reverse("billing:home"), {"status": "unpaid"})
        self.assertContains(unpaid, self.invoice.number)
        self.assertContains(unpaid, "Unpaid")

    def test_employee_cannot_record_a_customer_payment(self):
        employee = get_user_model().objects.create_user(
            username="payment-employee",
            password="test-password",
        )
        UserProfile.objects.create(
            user=employee,
            role=UserProfile.Role.EMPLOYEE,
            factory=self.factory,
        )
        self.client.force_login(employee)

        response = self.client.post(
            self.detail_url,
            {
                "action": "record_payment",
                "payment_date": timezone.localdate().isoformat(),
                "amount": "25.00",
                "method": "cash",
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(BillingPayment.objects.exists())


class PurchaseBillWorkflowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="purchase-admin",
            email="purchase@example.com",
            password="test-password",
        )
        self.client.force_login(self.user)
        self.company = Company.objects.create(name="Purchase Company")
        self.factory = Factory.objects.create(
            company=self.company,
            name="Purchase Factory",
            factory_code="PUR",
            gstin="27AAPFU0939F1ZV",
            state_code="27",
        )
        self.supplier = Supplier.objects.create(
            factory=self.factory,
            name="Steel Supplier",
            gstin="27AAPFU0939F1ZV",
            state="Maharashtra",
            state_code="27",
        )
        self.item = InventoryItem.objects.create(
            factory=self.factory,
            sku="STEEL-01",
            name="Steel coil",
            unit_of_measure="kg",
        )
        self.warehouse = Warehouse.objects.create(
            factory=self.factory,
            code="MAIN",
            name="Main warehouse",
        )
        self.category = ProductCategory.objects.create(name="Purchase materials")
        self.product = Product.objects.create(
            sku="MAT-01",
            name="Steel material",
            category=self.category,
            tax_rate=Decimal("18"),
            hsn_sac_code="720810",
        )

    def create_bill(self, **overrides):
        data = {
            "factory": self.factory.pk,
            "supplier": self.supplier.pk,
            "supplier_invoice_number": "SUP-INV-1001",
            "issue_date": "2026-11-05",
            "due_date": "2026-12-05",
            "payment_terms": "Net 30",
            "discount_amount": "10",
            "currency_code": "INR",
            "notes": "Steel coil delivery",
            "lines": json.dumps([
                {
                    "description": "Steel material",
                    "product": str(self.product.pk),
                    "inventory_item": str(self.item.pk),
                    "quantity": "2",
                    "unit_price": "100",
                    "tax_rate": "18",
                    "hsn_sac_code": "720810",
                },
            ]),
        }
        data.update(overrides)
        return self.client.post(reverse("billing:purchase_bill_create"), data)

    def test_supplier_bill_saves_input_tax_and_links_procurement_inventory(self):
        order = ProcurementDocument.objects.create(
            factory=self.factory,
            number="PO-PUR-001",
            kind=ProcurementDocument.Kind.ORDER,
            status=ProcurementDocument.Status.ORDERED,
            item=self.item,
            warehouse=self.warehouse,
            supplier=self.supplier,
            quantity=Decimal("10"),
        )
        response = self.client.get(reverse("billing:purchase_bill_create"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "input tax")
        self.assertContains(response, "Purchase order")

        response = self.create_bill(procurement_document=order.pk)
        self.assertEqual(response.status_code, 302)
        bill = BillingDocument.objects.get()
        self.assertEqual(bill.number, "BILL-PUR-26-00001")
        self.assertEqual(bill.supplier_invoice_number, "SUP-INV-1001")
        self.assertEqual(bill.kind, BillingDocument.Kind.SUPPLIER_BILL)
        self.assertEqual(bill.status, BillingDocument.Status.DRAFT)
        self.assertEqual(bill.supplier, self.supplier)
        self.assertEqual(bill.procurement_document, order)
        self.assertEqual(bill.subtotal, Decimal("200.00"))
        self.assertEqual(bill.taxable_value, Decimal("190.00"))
        self.assertEqual(bill.cgst_amount, Decimal("17.10"))
        self.assertEqual(bill.sgst_amount, Decimal("17.10"))
        self.assertEqual(bill.igst_amount, Decimal("0.00"))
        self.assertEqual(bill.total, Decimal("224.20"))
        line = bill.lines.get()
        self.assertEqual(line.inventory_item, self.item)
        self.assertEqual(line.product, self.product)
        self.assertEqual(line.hsn_sac_code, "720810")

        detail = self.client.get(reverse("billing:document_detail", args=[bill.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Verify supplier bill")
        self.assertContains(detail, "PO-PUR-001")
        self.assertContains(detail, "Supplier invoice: SUP-INV-1001")
        self.assertEqual(bill.balance_due, bill.total)

        printed = self.client.get(reverse("billing:purchase_bill_pdf", args=[bill.pk]))
        self.assertEqual(printed.status_code, 200)
        self.assertEqual(printed["Content-Type"], "application/pdf")
        self.assertTrue(printed.content.startswith(b"%PDF"))
        downloaded = self.client.get(reverse("billing:purchase_bill_pdf_download", args=[bill.pk]))
        self.assertIn("attachment", downloaded["Content-Disposition"])

    def test_verification_audit_supplier_balance_and_overdue_tracking(self):
        self.create_bill(
            issue_date=(timezone.localdate() - timedelta(days=2)).isoformat(),
            due_date=(timezone.localdate() - timedelta(days=1)).isoformat(),
            discount_amount="0",
        )
        bill = BillingDocument.objects.get()
        verified = self.client.post(reverse("billing:purchase_bill_verify", args=[bill.pk]))
        self.assertEqual(verified.status_code, 302)
        bill.refresh_from_db()
        self.supplier.refresh_from_db()
        self.assertEqual(bill.status, BillingDocument.Status.APPROVED)
        self.assertEqual(bill.approved_by, self.user)
        self.assertIsNotNone(bill.approved_at)
        self.assertEqual(bill.payment_status, BillingDocument.PaymentStatus.OVERDUE)
        self.assertEqual(self.supplier.outstanding_balance, bill.total)
        event = UserActivity.objects.get(action="purchase_bill_verified")
        self.assertEqual(event.details["from_status"], BillingDocument.Status.DRAFT)
        self.assertEqual(event.details["to_status"], BillingDocument.Status.APPROVED)
        self.assertContains(
            self.client.get(reverse("billing:home"), {"kind": "supplier_bill", "status": "overdue"}),
            bill.number,
        )
        self.assertContains(
            self.client.get(reverse("billing:home")),
            "Supplier balances due",
        )

    def test_supplier_invoice_pdf_upload_and_scoped_download(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            uploaded_pdf = SimpleUploadedFile(
                "supplier-original.pdf",
                b"%PDF-1.4\nSupplier invoice\n%%EOF",
                content_type="application/pdf",
            )
            self.create_bill(supplier_invoice_pdf=uploaded_pdf)
            bill = BillingDocument.objects.get()

            response = self.client.get(
                reverse("billing:purchase_bill_source_pdf", args=[bill.pk])
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Content-Type"], "application/pdf")
            self.assertIn("attachment", response["Content-Disposition"])
            self.assertEqual(
                b"".join(response.streaming_content),
                b"%PDF-1.4\nSupplier invoice\n%%EOF",
            )
            self.assertContains(
                self.client.get(reverse("billing:document_detail", args=[bill.pk])),
                "Download supplier invoice",
            )

    def test_supplier_invoice_upload_rejects_non_pdf_files(self):
        upload = SimpleUploadedFile(
            "supplier-invoice.pdf",
            b"not a PDF",
            content_type="application/pdf",
        )
        response = self.create_bill(supplier_invoice_pdf=upload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(BillingDocument.objects.exists())
        self.assertContains(response, "not a valid PDF")

    def test_verified_supplier_bill_accepts_payments_and_exposes_payment_history(self):
        self.create_bill(discount_amount="0")
        bill = BillingDocument.objects.get()
        self.client.post(reverse("billing:purchase_bill_verify", args=[bill.pk]))

        detail_url = reverse("billing:document_detail", args=[bill.pk])
        self.assertContains(self.client.get(detail_url), "Record supplier payment")
        self.client.post(
            detail_url,
            {
                "action": "record_payment",
                "payment_date": timezone.localdate().isoformat(),
                "amount": "100.00",
                "method": "bank_transfer",
                "reference": "SUPPLIER-PAY-01",
                "notes": "",
            },
        )
        bill.refresh_from_db()
        self.supplier.refresh_from_db()
        self.assertEqual(bill.status, BillingDocument.Status.PART_PAID)
        self.assertEqual(bill.amount_paid, Decimal("100.00"))
        self.assertEqual(self.supplier.outstanding_balance, bill.total - Decimal("100.00"))
        payment = bill.payments.get()
        self.assertTrue(
            UserActivity.objects.filter(action="supplier_bill_payment_recorded").exists()
        )
        receipt = self.client.get(
            reverse("billing:payment_receipt", args=[payment.pk])
        )
        self.assertEqual(receipt.status_code, 200)
        self.assertTrue(receipt.content.startswith(b"%PDF"))
        self.assertContains(self.client.get(detail_url), "SUPPLIER-PAY-01")

        self.client.post(
            detail_url,
            {
                "action": "record_payment",
                "payment_date": timezone.localdate().isoformat(),
                "amount": str(bill.total - Decimal("100.00")),
                "method": "bank_transfer",
                "reference": "SUPPLIER-PAY-02",
                "notes": "",
            },
        )
        bill.refresh_from_db()
        self.supplier.refresh_from_db()
        self.assertEqual(bill.status, BillingDocument.Status.PAID)
        self.assertEqual(self.supplier.outstanding_balance, Decimal("0.00"))

    def test_duplicate_supplier_invoice_number_and_mismatched_purchase_order_are_rejected(self):
        self.create_bill()
        original = BillingDocument.objects.get()
        response = self.create_bill()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(BillingDocument.objects.count(), 1)
        self.assertContains(response, "already registered")

        other_supplier = Supplier.objects.create(
            factory=self.factory,
            name="Other supplier",
            gstin=self.supplier.gstin,
            state_code="27",
        )
        order = ProcurementDocument.objects.create(
            factory=self.factory,
            number="PO-PUR-002",
            kind=ProcurementDocument.Kind.ORDER,
            item=self.item,
            warehouse=self.warehouse,
            supplier=other_supplier,
            quantity=Decimal("10"),
        )
        response = self.create_bill(
            supplier_invoice_number="SUP-INV-1002",
            procurement_document=order.pk,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(BillingDocument.objects.get(pk=original.pk).supplier, self.supplier)
        self.assertContains(response, "Purchase order supplier must match")

    def test_taxable_bill_requires_valid_supplier_gstin_and_hsn(self):
        self.supplier.gstin = ""
        self.supplier.save(update_fields=["gstin"])
        response = self.create_bill()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(BillingDocument.objects.exists())
        self.assertContains(response, "supplier GSTIN")

        self.supplier.gstin = "27AAPFU0939F1ZV"
        self.supplier.save(update_fields=["gstin"])
        response = self.create_bill(
            supplier_invoice_number="SUP-INV-1003",
            lines=json.dumps([{
                "description": "Manual item",
                "quantity": "1",
                "unit_price": "100",
                "tax_rate": "18",
            }]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(BillingDocument.objects.exists())
        self.assertContains(response, "HSN/SAC")

    def test_supplier_form_validates_gstin_against_supplier_state(self):
        response = self.client.post(
            reverse("operations:inventory_form", args=["supplier"]),
            {
                "factory": self.factory.pk,
                "name": "Invalid state supplier",
                "contact_name": "",
                "email": "",
                "phone": "",
                "address": "",
                "gstin": "27AAPFU0939F1ZV",
                "state": "Karnataka",
                "state_code": "29",
                "is_active": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Supplier.objects.filter(name="Invalid state supplier").exists())
        self.assertContains(response, "must match the supplier state code")


class CreditDebitNoteWorkflowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="adjustment-admin",
            email="adjustments@example.com",
            password="test-password",
        )
        self.client.force_login(self.user)
        self.company = Company.objects.create(name="Adjustment Company")
        self.factory = Factory.objects.create(
            company=self.company,
            name="Adjustment Factory",
            factory_code="ADJ",
            gstin="27AAPFU0939F1ZV",
            state_code="27",
        )
        self.customer = Customer.objects.create(
            company=self.company,
            name="Adjustment Customer",
            state_code="27",
        )
        self.supplier = Supplier.objects.create(
            factory=self.factory,
            name="Adjustment Supplier",
            gstin="27AAPFU0939F1ZV",
            state_code="27",
        )

    def create_source_document(self, kind, status):
        document = BillingDocument.objects.create(
            factory=self.factory,
            number=f"{'INV' if kind == BillingDocument.Kind.SALES_INVOICE else 'BILL'}-ADJ-001",
            kind=kind,
            status=status,
            customer=self.customer if kind == BillingDocument.Kind.SALES_INVOICE else None,
            supplier=self.supplier if kind == BillingDocument.Kind.SUPPLIER_BILL else None,
            issue_date=timezone.localdate() - timedelta(days=3),
            total=Decimal("236.00"),
            subtotal=Decimal("200.00"),
            cgst_amount=Decimal("18.00"),
            sgst_amount=Decimal("18.00"),
            tax_amount=Decimal("36.00"),
            currency_code="INR",
        )
        BillingLine.objects.create(
            document=document,
            description="Steel part",
            quantity=Decimal("2"),
            unit_price=Decimal("100.00"),
            line_total=Decimal("200.00"),
            taxable_value=Decimal("200.00"),
            tax_rate=Decimal("18.000"),
            hsn_sac_code="720810",
            cgst_amount=Decimal("18.00"),
            sgst_amount=Decimal("18.00"),
        )
        return document

    def create_adjustment(self, document, quantity="1", reason="Returned damaged goods"):
        return self.client.post(
            reverse("billing:adjustment_create", args=[document.pk]),
            {
                "issue_date": timezone.localdate().isoformat(),
                "reason": reason,
                "lines": json.dumps([{
                    "source_line": document.lines.get().pk,
                    "quantity": quantity,
                }]),
            },
        )

    def test_sales_return_creates_credit_note_and_reverses_original_gst_on_approval(self):
        invoice = self.create_source_document(
            BillingDocument.Kind.SALES_INVOICE,
            BillingDocument.Status.ISSUED,
        )
        response = self.client.get(
            reverse("billing:adjustment_create", args=[invoice.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Credit note")

        response = self.create_adjustment(invoice)
        self.assertEqual(response.status_code, 302)
        note = CreditDebitNote.objects.get()
        line = note.lines.get()
        self.assertEqual(note.kind, CreditDebitNote.Kind.CREDIT_NOTE)
        self.assertEqual(note.source_document, invoice)
        self.assertEqual(note.reason, "Returned damaged goods")
        self.assertEqual(note.status, CreditDebitNote.Status.DRAFT)
        self.assertEqual(note.taxable_value, Decimal("100.00"))
        self.assertEqual(note.cgst_amount, Decimal("9.00"))
        self.assertEqual(note.sgst_amount, Decimal("9.00"))
        self.assertEqual(note.total, Decimal("118.00"))
        self.assertEqual(line.source_line, invoice.lines.get())
        self.assertEqual(invoice.balance_due, Decimal("236.00"))

        self.client.post(reverse("billing:adjustment_approve", args=[note.pk]))
        note.refresh_from_db()
        invoice.refresh_from_db()
        self.assertEqual(note.status, CreditDebitNote.Status.APPROVED)
        self.assertEqual(note.approved_by, self.user)
        self.assertIsNotNone(note.approved_at)
        self.assertEqual(invoice.balance_due, Decimal("118.00"))
        self.assertEqual(self.customer.outstanding_balance, Decimal("118.00"))
        self.assertTrue(
            UserActivity.objects.filter(action="billing_adjustment_approved").exists()
        )
        self.assertContains(
            self.client.get(reverse("billing:document_detail", args=[invoice.pk])),
            note.number,
        )
        self.assertContains(
            self.client.get(reverse("billing:adjustment_list")),
            "Returned damaged goods",
        )

        self.create_adjustment(invoice)
        second_note = CreditDebitNote.objects.exclude(pk=note.pk).get()
        self.client.post(reverse("billing:adjustment_approve", args=[second_note.pk]))
        invoice.refresh_from_db()
        self.assertEqual(invoice.balance_due, Decimal("0.00"))
        self.assertEqual(second_note.total, Decimal("118.00"))

    def test_purchase_return_creates_debit_note_and_rejects_over_returned_quantity(self):
        bill = self.create_source_document(
            BillingDocument.Kind.SUPPLIER_BILL,
            BillingDocument.Status.APPROVED,
        )
        response = self.client.get(reverse("billing:adjustment_create", args=[bill.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Debit note")

        self.create_adjustment(bill, quantity="3")
        self.assertFalse(CreditDebitNote.objects.exists())

        self.create_adjustment(bill, quantity="1", reason="Supplier return")
        note = CreditDebitNote.objects.get()
        self.assertEqual(note.kind, CreditDebitNote.Kind.DEBIT_NOTE)
        self.assertEqual(note.total, Decimal("118.00"))
        self.client.post(reverse("billing:adjustment_approve", args=[note.pk]))
        note.refresh_from_db()
        bill.refresh_from_db()
        self.assertEqual(note.status, CreditDebitNote.Status.APPROVED)
        self.assertEqual(bill.balance_due, Decimal("118.00"))
        self.assertEqual(self.supplier.outstanding_balance, Decimal("118.00"))
        self.assertContains(
            self.client.get(reverse("billing:adjustment_detail", args=[note.pk])),
            "Tax adjustment",
        )

    def test_approval_rechecks_quantity_when_other_draft_is_approved_first(self):
        invoice = self.create_source_document(
            BillingDocument.Kind.SALES_INVOICE,
            BillingDocument.Status.ISSUED,
        )
        self.create_adjustment(invoice, quantity="1")
        first_note = CreditDebitNote.objects.get()
        self.create_adjustment(invoice, quantity="2")
        second_note = CreditDebitNote.objects.exclude(pk=first_note.pk).get()

        self.client.post(reverse("billing:adjustment_approve", args=[second_note.pk]))
        self.client.post(reverse("billing:adjustment_approve", args=[first_note.pk]))
        first_note.refresh_from_db()
        self.assertEqual(first_note.status, CreditDebitNote.Status.DRAFT)
        self.assertContains(
            self.client.get(reverse("billing:adjustment_detail", args=[first_note.pk])),
            "only 0.000 remains available",
        )

    def test_billing_reports_export_audited_csv_pdf_and_signed_gst_summary(self):
        invoice = self.create_source_document(
            BillingDocument.Kind.SALES_INVOICE,
            BillingDocument.Status.ISSUED,
        )
        self.customer.name = "=HYPERLINK(\"https://example.invalid\")"
        self.customer.save(update_fields=["name"])
        bill = self.create_source_document(
            BillingDocument.Kind.SUPPLIER_BILL,
            BillingDocument.Status.APPROVED,
        )
        bill.number = "BILL-ADJ-002"
        bill.save(update_fields=["number"])

        page = self.client.get(reverse("reports:billing"), {"report": "sales_register"})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, invoice.number)

        csv_export = self.client.get(
            reverse("reports:billing"),
            {"report": "sales_register", "format": "csv"},
        )
        self.assertEqual(csv_export.status_code, 200)
        self.assertIn(b"'=HYPERLINK", csv_export.content)
        self.assertTrue(
            UserActivity.objects.filter(
                action="billing_report_exported",
                details__report="sales_register",
                details__format="csv",
            ).exists()
        )

        pdf_export = self.client.get(
            reverse("reports:billing"),
            {"report": "purchase_register", "format": "pdf"},
        )
        self.assertEqual(pdf_export.status_code, 200)
        self.assertTrue(pdf_export.content.startswith(b"%PDF-1.4"))
        self.assertIn(b"/Title (Purchase register)", pdf_export.content)
        self.assertIn(b"/Type /Page", pdf_export.content)

        gst_report = self.client.get(
            reverse("reports:billing"),
            {"report": "gst_summary"},
        )
        self.assertContains(gst_report, "Purchase bill · input tax")
        self.assertContains(gst_report, "-18.00")
        self.assertContains(gst_report, invoice.number)
        self.assertContains(gst_report, bill.number)

    def test_billing_reports_are_factory_scoped_and_reject_non_managers(self):
        own_invoice = self.create_source_document(
            BillingDocument.Kind.SALES_INVOICE,
            BillingDocument.Status.ISSUED,
        )
        other_factory = Factory.objects.create(
            company=self.company,
            name="Other Factory",
            factory_code="OTHER",
            state_code="27",
        )
        BillingDocument.objects.create(
            factory=other_factory,
            number="INV-OTHER-001",
            kind=BillingDocument.Kind.SALES_INVOICE,
            status=BillingDocument.Status.ISSUED,
            customer=self.customer,
            issue_date=timezone.localdate(),
            total=Decimal("100.00"),
        )
        manager = get_user_model().objects.create_user(
            username="factory-report-manager",
            password="test-password",
        )
        profile = UserProfile.objects.create(
            user=manager,
            role=UserProfile.Role.FACTORY_MANAGER,
            factory=self.factory,
        )
        self.client.force_login(manager)

        response = self.client.get(reverse("reports:billing"), {"report": "sales_register"})
        self.assertContains(response, own_invoice.number)
        self.assertNotContains(response, "INV-OTHER-001")

        profile.role = UserProfile.Role.EMPLOYEE
        profile.save(update_fields=["role"])
        forbidden = self.client.get(reverse("reports:billing"))
        self.assertEqual(forbidden.status_code, 403)

    def test_executive_dashboard_includes_scoped_billing_metrics_and_charts(self):
        invoice = self.create_source_document(
            BillingDocument.Kind.SALES_INVOICE,
            BillingDocument.Status.PART_PAID,
        )
        invoice.amount_paid = Decimal("36.00")
        invoice.save(update_fields=["amount_paid"])
        BillingPayment.objects.create(
            document=invoice,
            payment_date=timezone.localdate(),
            amount=Decimal("36.00"),
            method=BillingPayment.Method.BANK_TRANSFER,
            recorded_by=self.user,
        )

        response = self.client.get(
            reverse("core:executive_dashboard"),
            {"month": invoice.issue_date.strftime("%Y-%m"), "currency": "INR"},
        )
        self.assertEqual(response.status_code, 200)
        metrics = response.context["billing_metrics"]
        self.assertEqual(metrics["total_sales"], Decimal("236.00"))
        self.assertEqual(metrics["monthly_revenue"], Decimal("236.00"))
        self.assertEqual(metrics["amount_received"], Decimal("36.00"))
        self.assertEqual(metrics["outstanding_receivables"], Decimal("200.00"))
        self.assertEqual(metrics["total_gst"], Decimal("36.00"))
        self.assertEqual(metrics["total_invoices"], 1)
        self.assertContains(response, "Monthly sales trend")
        self.assertContains(response, "Supplier payment trends")
        self.assertIn("customer_sales", response.context["billing_charts"])

    def test_invalid_billing_report_dates_are_rejected(self):
        response = self.client.get(
            reverse("reports:billing"),
            {"report": "sales_register", "start": "not-a-date"},
        )
        self.assertEqual(response.status_code, 400)

    def test_customer_ledger_keeps_running_balances_separate_by_party(self):
        first_invoice = self.create_source_document(
            BillingDocument.Kind.SALES_INVOICE,
            BillingDocument.Status.ISSUED,
        )
        second_customer = Customer.objects.create(
            company=self.company,
            name="Second Customer",
            state_code="27",
        )
        second_invoice = BillingDocument.objects.create(
            factory=self.factory,
            number="INV-ADJ-002",
            kind=BillingDocument.Kind.SALES_INVOICE,
            status=BillingDocument.Status.ISSUED,
            customer=second_customer,
            issue_date=first_invoice.issue_date,
            total=Decimal("100.00"),
            currency_code="INR",
        )

        response = self.client.get(
            reverse("reports:billing"),
            {"report": "customer_ledger"},
        )
        rows = response.context["page_obj"].object_list
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(rows), 2)
        self.assertEqual([row[-1] for row in rows], [Decimal("236.00"), Decimal("100.00")])
        self.assertEqual({row[2] for row in rows}, {self.customer.name, second_customer.name})
