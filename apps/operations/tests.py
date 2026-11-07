from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import UserProfile
from apps.organization.models import Company, Department, Factory, ProductionUnit
from .models import (
    InventoryItem,
    Product,
    ProductCategory,
    ProductMaterialRequirement,
    ProductionPlan,
    ProcurementDocument,
    StockMovement,
    Supplier,
    Warehouse,
)


class RawMaterialPlanningTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="factory-admin",
            password="test-password",
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(self.user)
        self.company = Company.objects.create(name="Factory Company")
        self.factory = Factory.objects.create(
            company=self.company,
            name="Main Factory",
            factory_code="MAIN",
        )
        self.department = Department.objects.create(
            factory=self.factory,
            name="Production",
            department_code="PROD",
        )
        self.unit = ProductionUnit.objects.create(
            department=self.department,
            name="Assembly",
            unit_code="ASM",
        )
        self.category = ProductCategory.objects.create(name="Finished goods")
        self.product = Product.objects.create(
            sku="FG-001",
            name="Finished product",
            category=self.category,
        )
        self.material = InventoryItem.objects.create(
            factory=self.factory,
            sku="RM-001",
            name="Steel sheet",
            kind=InventoryItem.Kind.RAW,
            unit_of_measure="kg",
        )
        self.warehouse = Warehouse.objects.create(
            factory=self.factory,
            code="WH-1",
            name="Main warehouse",
        )
        self.supplier = Supplier.objects.create(
            factory=self.factory,
            name="Steel supplier",
        )
        today = timezone.localdate()
        self.plan = ProductionPlan.objects.create(
            factory=self.factory,
            department=self.department,
            production_unit=self.unit,
            product=self.product,
            name="November production",
            start_date=today,
            end_date=today + timedelta(days=7),
            target_quantity=10,
            created_by=self.user,
        )

    def save_requirement(self, quantity="2.000"):
        return self.client.post(
            reverse("operations:material_planning"),
            {
                "action": "save_requirement",
                "production_plan": self.plan.pk,
                "factory": self.factory.pk,
                "product": self.product.pk,
                "raw_material": self.material.pk,
                "quantity_per_unit": quantity,
            },
        )

    def test_recipe_can_be_created_updated_and_removed(self):
        response = self.save_requirement()
        self.assertEqual(response.status_code, 302)
        requirement = ProductMaterialRequirement.objects.get()
        self.assertEqual(requirement.quantity_per_unit, Decimal("2.000"))

        response = self.save_requirement("3.000")
        self.assertEqual(response.status_code, 302)
        requirement.refresh_from_db()
        self.assertEqual(requirement.quantity_per_unit, Decimal("3.000"))
        self.assertEqual(ProductMaterialRequirement.objects.count(), 1)

        response = self.client.post(
            reverse("operations:material_planning"),
            {
                "action": "delete_requirement",
                "production_plan": self.plan.pk,
                "requirement_id": requirement.pk,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ProductMaterialRequirement.objects.exists())

    def test_plan_calculates_shortage_after_stock_and_open_orders(self):
        self.save_requirement()
        StockMovement.objects.create(
            item=self.material,
            warehouse=self.warehouse,
            kind=StockMovement.Kind.RECEIPT,
            quantity=Decimal("10.000"),
            created_by=self.user,
        )
        ProcurementDocument.objects.create(
            factory=self.factory,
            number="PO-INCOMING-001",
            kind=ProcurementDocument.Kind.ORDER,
            status=ProcurementDocument.Status.ORDERED,
            item=self.material,
            warehouse=self.warehouse,
            supplier=self.supplier,
            quantity=Decimal("5.000"),
            production_plan=self.plan,
            created_by=self.user,
        )

        response = self.client.get(
            reverse("operations:material_planning"),
            {"production_plan": self.plan.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["material_rows"][0]["required"], Decimal("20.000"))
        self.assertEqual(response.context["material_rows"][0]["on_hand"], Decimal("10.000"))
        self.assertEqual(response.context["material_rows"][0]["incoming"], Decimal("5.000"))
        self.assertEqual(response.context["material_rows"][0]["shortage"], Decimal("5.000"))

    def test_shortage_creates_receivable_purchase_order(self):
        self.save_requirement()
        requirement = ProductMaterialRequirement.objects.get()
        response = self.client.post(
            reverse(
                "operations:raw_material_purchase_order",
                args=[self.plan.pk, requirement.pk],
            ),
            {
                "supplier": self.supplier.pk,
                "warehouse": self.warehouse.pk,
                "quantity": "20.000",
                "expected_date": "",
                "notes": "Production material",
            },
        )

        self.assertEqual(response.status_code, 302)
        order = ProcurementDocument.objects.get()
        self.assertEqual(order.kind, ProcurementDocument.Kind.ORDER)
        self.assertEqual(order.status, ProcurementDocument.Status.ORDERED)
        self.assertEqual(order.factory, self.factory)
        self.assertEqual(order.item, self.material)
        self.assertEqual(order.supplier, self.supplier)
        self.assertEqual(order.warehouse, self.warehouse)
        self.assertEqual(order.production_plan, self.plan)
        self.assertEqual(order.quantity, Decimal("20.000"))

        response = self.client.post(
            reverse("operations:inventory_form", args=["movement"]),
            {
                "item": self.material.pk,
                "warehouse": self.warehouse.pk,
                "kind": StockMovement.Kind.RECEIPT,
                "quantity": "20.000",
                "unit_cost": "12.50",
                "procurement_document": order.pk,
                "reference": "DELIVERY-001",
                "notes": "Received against purchase order",
            },
        )
        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, ProcurementDocument.Status.RECEIVED)
        self.assertEqual(order.receipts.count(), 1)

    def test_employee_cannot_manage_material_plans(self):
        employee = get_user_model().objects.create_user(
            username="worker",
            password="test-password",
        )
        UserProfile.objects.create(
            user=employee,
            role=UserProfile.Role.EMPLOYEE,
            factory=self.factory,
            department=self.department,
        )
        self.client.force_login(employee)

        response = self.client.get(reverse("operations:material_planning"))

        self.assertEqual(response.status_code, 404)

    def test_supply_chain_dashboard_renders_for_factory_manager(self):
        response = self.client.get(reverse("operations:supply_chain"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Supply chain intelligence")
        self.assertContains(response, "Material demand &amp; stock cover")
        self.assertContains(response, self.material.name)
        self.assertContains(response, "Expected deliveries")
