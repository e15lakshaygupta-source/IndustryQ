from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import UserProfile
from apps.operations.models import Customer, Product, ProductCategory
from apps.organization.models import Company, Factory
from .models import Enquiry, FollowUp, Lead, Quotation, QuotationLine


class CRMWorkflowTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_superuser(
            username="crm-admin",
            email="crm@example.com",
            password="test-password",
        )
        self.client.force_login(self.user)
        self.company = Company.objects.create(name="Example Manufacturing")
        self.factory = Factory.objects.create(
            company=self.company,
            name="Main Factory",
            factory_code="CRM",
        )
        self.customer = Customer.objects.create(
            company=self.company,
            name="Existing Customer",
        )
        self.category = ProductCategory.objects.create(name="CRM products")
        self.product = Product.objects.create(
            sku="CRM-P1",
            name="Industrial pump",
            category=self.category,
            selling_price=Decimal("1000"),
        )

    def create_lead(self):
        response = self.client.post(
            reverse("crm:lead_create"),
            {
                "factory": self.factory.pk,
                "customer": "",
                "company_name": "Prospect Industries",
                "contact_name": "A. Contact",
                "email": "contact@example.com",
                "phone": "1234567890",
                "source": "Website",
                "stage": Lead.Stage.QUALIFIED,
                "estimated_value": "25000.00",
                "expected_close_date": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        return Lead.objects.get(company_name="Prospect Industries")

    def test_dashboard_shows_crm_records_and_existing_customers(self):
        lead = self.create_lead()
        Enquiry.objects.create(
            factory=self.factory,
            lead=lead,
            subject="Pump capacity enquiry",
            details="Need a pump for the production line.",
        )

        response = self.client.get(reverse("crm:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sales pipeline")
        self.assertContains(response, "Prospect Industries")
        self.assertContains(response, "Existing Customer")
        self.assertContains(response, "Pump capacity enquiry")

    def test_enquiry_and_line_item_quotation_are_created_for_lead(self):
        lead = self.create_lead()
        enquiry_response = self.client.post(
            reverse("crm:enquiry_create"),
            {
                "lead": lead.pk,
                "subject": "Pump capacity enquiry",
                "details": "Please advise on the right model.",
                "status": Enquiry.Status.IN_PROGRESS,
                "received_at": timezone.localdate().isoformat(),
            },
        )
        self.assertEqual(enquiry_response.status_code, 302)
        enquiry = Enquiry.objects.get()
        self.assertEqual(enquiry.factory, self.factory)

        quotation_response = self.client.post(
            reverse("crm:quotation_create"),
            {
                "lead": lead.pk,
                "valid_until": (timezone.localdate() + timedelta(days=14)).isoformat(),
                "notes": "Delivery subject to confirmation.",
                "lines-TOTAL_FORMS": "1",
                "lines-INITIAL_FORMS": "0",
                "lines-MIN_NUM_FORMS": "1",
                "lines-MAX_NUM_FORMS": "1000",
                "lines-0-product": self.product.pk,
                "lines-0-description": "Industrial pump",
                "lines-0-quantity": "2",
                "lines-0-unit_price": "1000",
                "lines-0-tax_rate": "18",
            },
        )
        self.assertEqual(quotation_response.status_code, 302)
        quotation = Quotation.objects.get()
        self.assertEqual(quotation.factory, self.factory)
        self.assertEqual(quotation.lead, lead)
        self.assertTrue(quotation.number.startswith("QT-CRM-"))
        self.assertEqual(quotation.lines.count(), 1)
        self.assertEqual(quotation.total, Decimal("2360.00"))
        response = self.client.post(
            reverse("crm:quotation_status_update", args=[quotation.pk]),
            {"status": Quotation.Status.SENT},
        )
        self.assertEqual(response.status_code, 302)
        quotation.refresh_from_db()
        self.assertEqual(quotation.status, Quotation.Status.SENT)

        response = self.client.post(
            reverse("crm:lead_stage_update", args=[lead.pk]),
            {"stage": Lead.Stage.NEGOTIATION},
        )
        self.assertEqual(response.status_code, 302)
        lead.refresh_from_db()
        self.assertEqual(lead.stage, Lead.Stage.NEGOTIATION)

        response = self.client.post(
            reverse("crm:enquiry_status_update", args=[enquiry.pk]),
            {"status": Enquiry.Status.QUOTED},
        )
        self.assertEqual(response.status_code, 302)
        enquiry.refresh_from_db()
        self.assertEqual(enquiry.status, Enquiry.Status.QUOTED)

    def test_follow_up_can_be_scheduled_and_completed(self):
        lead = self.create_lead()
        due_at = timezone.now() + timedelta(days=1)
        response = self.client.post(
            reverse("crm:follow_up_create"),
            {
                "lead": lead.pk,
                "customer": "",
                "subject": "Call about pump options",
                "notes": "Discuss delivery schedule.",
                "due_at": due_at.strftime("%Y-%m-%dT%H:%M"),
                "assigned_to": self.user.pk,
            },
        )
        self.assertEqual(response.status_code, 302)
        follow_up = FollowUp.objects.get()
        self.assertEqual(follow_up.factory, self.factory)
        self.assertEqual(follow_up.assigned_to, self.user)

        response = self.client.post(
            reverse("crm:follow_up_complete", args=[follow_up.pk])
        )
        self.assertEqual(response.status_code, 302)
        follow_up.refresh_from_db()
        self.assertEqual(follow_up.status, FollowUp.Status.COMPLETED)

    def test_lead_cannot_link_customer_from_another_company(self):
        other_company = Company.objects.create(name="Different Company")
        other_customer = Customer.objects.create(
            company=other_company,
            name="Out-of-scope customer",
        )

        response = self.client.post(
            reverse("crm:lead_create"),
            {
                "factory": self.factory.pk,
                "customer": other_customer.pk,
                "company_name": "Prospect Industries",
                "contact_name": "A. Contact",
                "email": "",
                "phone": "",
                "source": "",
                "stage": Lead.Stage.NEW,
                "estimated_value": "100",
                "expected_close_date": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Lead.objects.exists())

    def test_employee_cannot_view_or_update_an_unassigned_factory_lead(self):
        other_factory = Factory.objects.create(
            company=self.company,
            name="Other Factory",
            factory_code="CRM-2",
        )
        outside_lead = Lead.objects.create(
            factory=other_factory,
            company_name="Restricted Prospect",
            contact_name="Other Contact",
            created_by=self.user,
        )
        employee = get_user_model().objects.create_user(
            username="crm-employee",
            password="test-password",
        )
        UserProfile.objects.create(
            user=employee,
            role=UserProfile.Role.EMPLOYEE,
            factory=self.factory,
        )
        self.client.force_login(employee)

        response = self.client.get(reverse("crm:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Restricted Prospect")

        response = self.client.post(
            reverse("crm:lead_stage_update", args=[outside_lead.pk]),
            {"stage": Lead.Stage.WON},
        )
        self.assertEqual(response.status_code, 404)
        outside_lead.refresh_from_db()
        self.assertEqual(outside_lead.stage, Lead.Stage.NEW)
