from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import UserProfile
from apps.accounts.models import UserActivity
from apps.operations.models import BillingDocument
from apps.organization.models import Company, Department, Factory


class DashboardBillingSummaryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="factory-manager",
            password="test-password",
        )
        self.company = Company.objects.create(name="Dashboard company")
        self.factory = Factory.objects.create(
            company=self.company,
            name="Dashboard factory",
            factory_code="DASH",
        )
        UserProfile.objects.create(
            user=self.user,
            role=UserProfile.Role.FACTORY_MANAGER,
            factory=self.factory,
        )
        self.client.force_login(self.user)

    def create_invoice(self, *, status, due_date, total, amount_paid=Decimal("0")):
        return BillingDocument.objects.create(
            factory=self.factory,
            number=f"INV-DASH-26-{BillingDocument.objects.filter(factory=self.factory).count() + 1:05d}",
            kind=BillingDocument.Kind.SALES_INVOICE,
            status=status,
            issue_date=timezone.localdate(),
            due_date=due_date,
            total=total,
            amount_paid=amount_paid,
        )

    def test_manager_sees_scoped_invoice_status_and_balance(self):
        today = timezone.localdate()
        self.create_invoice(
            status=BillingDocument.Status.PART_PAID,
            due_date=today - timedelta(days=1),
            total=Decimal("125.00"),
            amount_paid=Decimal("25.00"),
        )
        self.create_invoice(
            status=BillingDocument.Status.ISSUED,
            due_date=today + timedelta(days=10),
            total=Decimal("80.00"),
            amount_paid=Decimal("30.00"),
        )
        self.create_invoice(
            status=BillingDocument.Status.DRAFT,
            due_date=today + timedelta(days=10),
            total=Decimal("50.00"),
        )
        other_company = Company.objects.create(name="Other dashboard company")
        other_factory = Factory.objects.create(
            company=other_company,
            name="Other dashboard factory",
            factory_code="OTHER",
        )
        BillingDocument.objects.create(
            factory=other_factory,
            number="INV-OTHER-00001",
            kind=BillingDocument.Kind.SALES_INVOICE,
            status=BillingDocument.Status.ISSUED,
            issue_date=today,
            due_date=today,
            total=Decimal("999.00"),
        )

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["billing_access"])
        self.assertEqual(response.context["billing_draft_count"], 1)
        self.assertEqual(response.context["billing_open_count"], 2)
        self.assertEqual(response.context["billing_overdue_count"], 1)
        self.assertEqual(response.context["billing_balance_due"], Decimal("150"))
        self.assertContains(response, "INDUSTRIQ")
        self.assertContains(response, "Create sales invoice")
        self.assertContains(response, reverse("billing:document_create"))

    def test_employee_without_billing_access_does_not_see_invoice_summary(self):
        self.user.profile.role = UserProfile.Role.EMPLOYEE
        self.user.profile.factory = None
        self.user.profile.save()
        self.create_invoice(
            status=BillingDocument.Status.ISSUED,
            due_date=timezone.localdate(),
            total=Decimal("125.00"),
        )

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["billing_access"])
        self.assertEqual(response.context["billing_open_count"], 0)
        self.assertNotContains(response, "Create sales invoice")


class ProfileSecurityAndAuditUiTests(TestCase):
    def setUp(self):
        self.password = "Initial-Strong-Password-824!"
        self.user = get_user_model().objects.create_user(
            username="profile-security-user",
            password=self.password,
            is_superuser=True,
        )
        self.client.force_login(self.user)

    def test_password_change_is_available_inside_profile(self):
        response = self.client.get(reverse("profile"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Password &amp; security")
        self.assertContains(response, 'name="action" value="change_password"')
        self.assertNotContains(response, "Change password <span")

    def test_password_change_updates_password_and_keeps_user_signed_in(self):
        new_password = "Updated-Strong-Password-824!"

        response = self.client.post(
            reverse("profile"),
            {
                "action": "change_password",
                "old_password": self.password,
                "new_password1": new_password,
                "new_password2": new_password,
            },
        )

        self.user.refresh_from_db()
        self.assertRedirects(response, "/profile/#security", fetch_redirect_response=False)
        self.assertTrue(self.user.check_password(new_password))
        self.assertTrue(response.wsgi_request.user.is_authenticated)
        self.assertTrue(UserActivity.objects.filter(user=self.user, action="password_changed").exists())

    def test_legacy_password_change_url_returns_to_profile_security(self):
        response = self.client.get(reverse("password_change"))

        self.assertRedirects(response, "/profile/#security", fetch_redirect_response=False)

    def test_audit_history_uses_filterable_paged_table(self):
        UserActivity.objects.create(
            user=self.user,
            action="profile_updated",
            details={"section": "contact"},
        )

        response = self.client.get(reverse("audit_history"), {"user": self.user.username})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Activity &amp; audit history")
        self.assertContains(response, "profile_updated")
        self.assertEqual(response.context["event_count"], 1)


class AuthenticationAccessCopyTests(TestCase):
    def test_login_explains_role_based_access_without_role_selector(self):
        response = self.client.get(reverse("login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<title>INDUSTRIQ | Industrial Operations</title>")
        self.assertNotContains(response, "IndustryOS")
        self.assertContains(response, "Employees, managers, company admins, and system admins")
        self.assertContains(response, "Your account permissions determine what you can access.")
        self.assertNotContains(response, 'name="role"')

    def test_registration_uses_the_induvanta_brand(self):
        response = self.client.get(reverse("register"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "INDUSTRIQ")
        self.assertNotContains(response, "IndustryOS")

    def test_self_registration_creates_an_employee_account(self):
        response = self.client.post(
            reverse("register"),
            {
                "username": "new-employee",
                "email": "new-employee@example.com",
                "first_name": "New",
                "last_name": "Employee",
                "password1": "Strong-Registration-Password-824!",
                "password2": "Strong-Registration-Password-824!",
            },
        )

        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)
        user = get_user_model().objects.get(username="new-employee")
        self.assertEqual(user.profile.role, UserProfile.Role.EMPLOYEE)

    def test_password_recovery_pages_use_the_shared_auth_layout(self):
        urls = [
            reverse("password_reset"),
            reverse("password_reset_done"),
            reverse(
                "password_reset_confirm",
                kwargs={"uidb64": "MQ", "token": "invalid-token"},
            ),
            reverse("password_reset_complete"),
        ]

        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'class="auth-simple-layout"')


class UserRoleAssignmentTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            username="system-admin",
            email="admin@example.com",
            password="Secure-Admin-Password-824!",
        )
        self.target = get_user_model().objects.create_user(
            username="staff-account",
            email="staff@example.com",
            password="Secure-Staff-Password-824!",
        )
        UserProfile.objects.create(user=self.target, role=UserProfile.Role.EMPLOYEE)
        self.company = Company.objects.create(name="Role assignment company")
        self.factory = Factory.objects.create(
            company=self.company,
            name="Role assignment factory",
            factory_code="ROLE",
        )
        self.department = Department.objects.create(
            factory=self.factory,
            name="Assembly",
            department_code="ASM",
        )
        self.client.force_login(self.admin)

    def test_dashboard_links_system_admin_to_user_role_management(self):
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("user_management"))
        management = self.client.get(reverse("user_management"))
        self.assertEqual(management.status_code, 200)
        self.assertContains(management, "Manage users &amp; roles")
        self.assertContains(management, "staff-account")

    def test_management_includes_accounts_without_profiles_and_searches_all_users(self):
        unprofiled_user = get_user_model().objects.create_user(
            username="unprofiled-account",
            email="new-account@example.com",
            password="Secure-Unprofiled-Password-824!",
        )

        response = self.client.get(reverse("user_management"), {"q": "new-account@example.com"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "unprofiled-account")
        self.assertEqual(unprofiled_user.profile.role, UserProfile.Role.EMPLOYEE)
        self.assertContains(response, "Employees to assign")
        self.assertContains(response, "Search accounts")

        assignment = self.client.post(
            reverse("user_assignment", args=[unprofiled_user.profile.pk]),
            {
                "role": UserProfile.Role.DEPARTMENT_MANAGER,
                "company": self.company.pk,
                "factory": self.factory.pk,
                "department": self.department.pk,
            },
        )

        self.assertRedirects(assignment, reverse("user_management"), fetch_redirect_response=False)
        unprofiled_user.profile.refresh_from_db()
        self.assertEqual(unprofiled_user.profile.role, UserProfile.Role.DEPARTMENT_MANAGER)

    def test_assignment_form_lists_all_factories_and_scopes_them_by_company(self):
        other_company = Company.objects.create(name="Second company")
        other_factory = Factory.objects.create(
            company=other_company,
            name="Second factory",
            factory_code="ROLE2",
        )

        response = self.client.get(reverse("user_management"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.factory.name)
        self.assertContains(response, other_factory.name)
        self.assertContains(response, 'data-company="')

    def test_system_admin_can_assign_manager_role_and_org_scope(self):
        response = self.client.post(
            reverse("user_assignment", args=[self.target.profile.pk]),
            {
                "role": UserProfile.Role.DEPARTMENT_MANAGER,
                "company": self.company.pk,
                "factory": self.factory.pk,
                "department": self.department.pk,
            },
        )

        self.assertRedirects(response, reverse("user_management"), fetch_redirect_response=False)
        self.target.profile.refresh_from_db()
        self.assertEqual(self.target.profile.role, UserProfile.Role.DEPARTMENT_MANAGER)
        self.assertEqual(self.target.profile.company, self.company)
        self.assertEqual(self.target.profile.factory, self.factory)
        self.assertEqual(self.target.profile.department, self.department)
        self.assertTrue(UserActivity.objects.filter(
            user=self.admin,
            action="user_role_assigned",
            details__target="staff-account",
        ).exists())

    def test_employee_cannot_open_user_role_management(self):
        self.client.force_login(self.target)

        response = self.client.get(reverse("user_management"))

        self.assertRedirects(response, reverse("dashboard"), fetch_redirect_response=False)

    def test_manager_assignment_requires_organization_scope(self):
        response = self.client.post(
            reverse("user_assignment", args=[self.target.profile.pk]),
            {
                "role": UserProfile.Role.FACTORY_MANAGER,
                "company": self.company.pk,
                "factory": "",
                "department": "",
            },
        )

        self.assertRedirects(response, reverse("user_management"), fetch_redirect_response=False)
        self.target.profile.refresh_from_db()
        self.assertEqual(self.target.profile.role, UserProfile.Role.EMPLOYEE)
        self.assertFalse(UserActivity.objects.filter(action="user_role_assigned").exists())
