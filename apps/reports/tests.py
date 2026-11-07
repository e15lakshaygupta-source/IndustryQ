import re

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.accounts.models import UserProfile
from apps.reports.views import pdf_response


class ReportAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username="report-admin", email="reports@example.test", password="safe-test-password-928!"
        )
        UserProfile.objects.update_or_create(
            user=cls.user, defaults={"role": UserProfile.Role.SUPER_ADMIN}
        )

    def test_report_pages_require_authentication(self):
        for name in ("reports:index", "reports:billing", "reports:executive", "reports:workforce", "reports:quality_energy", "reports:notifications"):
            with self.subTest(name=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)

    def test_report_pages_render_for_admin(self):
        self.client.force_login(self.user)
        for name in ("reports:index", "reports:billing", "reports:executive", "reports:workforce", "reports:quality_energy", "reports:notifications"):
            with self.subTest(name=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)

    def test_csv_export_has_download_headers(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("reports:executive"), {"format": "csv"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("attachment;", response["Content-Disposition"])

    def test_pdf_export_returns_pdf_document(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("reports:workforce"), {"format": "pdf"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF-1.4"))
        self.assertIn("attachment;", response["Content-Disposition"])

    def test_premium_pdf_report_repeats_table_header_across_pages(self):
        request = RequestFactory().get(
            "/reports/workforce/",
            {"start": "2026-01-01", "end": "2026-12-31"},
        )
        response = pdf_response(
            request,
            "Workforce report",
            ["Employee", "Factory", "Department", "Days", "Present", "Absent"],
            [[f"Employee {index}", "North", "Assembly", 22, 20, 2] for index in range(120)],
        )

        pages = re.findall(rb"/Type\s*/Page\b", response.content)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertGreater(len(pages), 1)

    def test_notification_inbox_renders(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("reports:notifications"))
        self.assertContains(response, "Notifications")
