import base64
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.accounts.models import Notification, UserActivity, UserProfile
from apps.intelligence.models import AssistantQuery, EnergyReading, Machine, MaintenanceRecord, QualityInspection, SafetyIncident
from apps.operations.models import InventoryItem, ProcurementDocument, Product, ProductCategory, ProductionEntry, ProductionPlan, StockMovement, Supplier, Warehouse, WorkOrder
from apps.organization.models import Company, Department, Factory, ProductionUnit
from apps.workforce.models import Attendance, Employee, EmployeeShift, LeaveRequest, PerformanceRecord, Shift


class Command(BaseCommand):
    help = "Create clearly labeled, idempotent INDUSTRIQ demo records."

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=5, help="Number of demo records per section (5 to 10).")

    def handle(self, *args, **options):
        count = options["count"]
        if not 5 <= count <= 10:
            raise CommandError("--count must be between 5 and 10.")
        today = timezone.localdate()
        now = timezone.now()
        User = get_user_model()
        actor = User.objects.filter(is_superuser=True).order_by("pk").first() or User.objects.filter(is_staff=True).order_by("pk").first()
        companies = []
        for i in range(1, count + 1):
            company, _ = Company.objects.get_or_create(
                registration_number=f"DEMO-IND-{i:04d}",
                defaults={"name": f"DEMO Industry Group {i:02d}", "industry_type": "Industrial Manufacturing", "email": f"demo.company{i:02d}@example.invalid", "city": "Pune", "state": "Maharashtra", "country": "India", "is_active": True},
            )
            companies.append(company)

        categories, products, factories, departments, units = [], [], [], [], []
        employees, shifts, plans, orders = [], [], [], []
        for i in range(1, count + 1):
            category, _ = ProductCategory.objects.get_or_create(name=f"DEMO Category {i:02d}", defaults={"description": "Seeded sample product category."})
            product, _ = Product.objects.get_or_create(sku=f"DEMO-SKU-{i:03d}", defaults={"name": f"Demo Industrial Part {i:02d}", "category": category, "description": "Sample catalogue item for demonstration.", "unit_of_measure": "units", "is_active": True})
            factory, _ = Factory.objects.get_or_create(company=companies[i-1], factory_code=f"DEMO-F{i:02d}", defaults={"name": f"DEMO Factory {i:02d}", "city": ["Pune", "Nashik", "Indore", "Surat", "Vadodara", "Jaipur", "Chennai", "Mysuru", "Noida", "Kochi"][i-1], "state": "Demo Region", "manager_name": f"Demo Manager {i:02d}", "production_capacity": Decimal("10000.00"), "is_active": True})
            department, _ = Department.objects.get_or_create(factory=factory, department_code=f"DEMO-D{i:02d}", defaults={"name": f"DEMO Assembly {i:02d}", "description": "Seeded sample production department.", "manager_name": f"Demo Lead {i:02d}", "is_active": True})
            unit, _ = ProductionUnit.objects.get_or_create(department=department, unit_code=f"DEMO-U{i:02d}", defaults={"name": f"DEMO Line {i:02d}", "description": "Seeded production line.", "is_active": True})
            employee, _ = Employee.objects.get_or_create(employee_id=f"DEMO-EMP-{i:03d}", defaults={"first_name": f"DemoEmployee{i:02d}", "last_name": "Sample", "email": f"demo.employee{i:02d}@example.invalid", "phone": f"+910000000{i:02d}", "factory": factory, "department": department, "designation": "Production Associate", "joining_date": today - timedelta(days=365-i), "employment_status": Employee.EmploymentStatus.ACTIVE})
            shift, _ = Shift.objects.get_or_create(factory=factory, shift_code=f"DEMO-S{i:02d}", defaults={"name": f"DEMO Day Shift {i:02d}", "start_time": time(8, 0), "end_time": time(16, 0), "description": "Seeded sample shift.", "is_active": True})
            EmployeeShift.objects.get_or_create(employee=employee, shift=shift, start_date=today-timedelta(days=30), defaults={"assigned_by": actor})
            plan, _ = ProductionPlan.objects.get_or_create(factory=factory, name=f"DEMO Plan {i:02d}", defaults={"department": department, "production_unit": unit, "product": product, "start_date": today-timedelta(days=count), "end_date": today+timedelta(days=30), "target_quantity": 1000+i*100, "status": ProductionPlan.Status.IN_PROGRESS, "created_by": actor})
            order, _ = WorkOrder.objects.get_or_create(order_number=f"DEMO-WO-{i:03d}", defaults={"plan": plan, "factory": factory, "department": department, "production_unit": unit, "product": product, "scheduled_start": now-timedelta(days=count-i), "scheduled_end": now-timedelta(days=count-i)+timedelta(hours=8), "planned_quantity": 100+i*20, "status": WorkOrder.Status.IN_PROGRESS, "notes": "Seeded sample production schedule.", "created_by": actor})
            categories.append(category); products.append(product); factories.append(factory); departments.append(department); units.append(unit); employees.append(employee); shifts.append(shift); plans.append(plan); orders.append(order)

        for i, employee in enumerate(employees, start=1):
            for day_index in range(count):
                record_date = today - timedelta(days=day_index)
                Attendance.objects.get_or_create(employee=employee, date=record_date, defaults={"shift": shifts[i-1], "status": [Attendance.Status.PRESENT, Attendance.Status.LATE, Attendance.Status.PRESENT, Attendance.Status.HALF_DAY, Attendance.Status.ABSENT][(i+day_index-2) % 5], "check_in": time(8, 0) if day_index else time(8, 5), "check_out": time(16, 0) if day_index else None, "remarks": "DEMO sample attendance"})
            leave_start = today + timedelta(days=10+i)
            LeaveRequest.objects.get_or_create(employee=employee, leave_type=LeaveRequest.LeaveType.CASUAL, start_date=leave_start, defaults={"end_date": leave_start+timedelta(days=1), "reason": "DEMO sample leave application.", "status": LeaveRequest.ApprovalStatus.PENDING})
            PerformanceRecord.objects.get_or_create(employee=employee, period_start=today-timedelta(days=30), period_end=today-timedelta(days=1), defaults={"recorded_by": actor, "score": Decimal(75+i*3), "goals_met": i+2, "notes": "DEMO sample performance review."})

        for i, (factory, department, unit, product, order) in enumerate(zip(factories, departments, units, products, orders), start=1):
            for day_index in range(count):
                entry_date = today - timedelta(days=day_index)
                ProductionEntry.objects.get_or_create(factory=factory, department=department, production_unit=unit, product=product, work_order=order, production_date=entry_date, defaults={"actual_quantity": 90+i*10+day_index, "rejected_quantity": 2+i%4, "downtime_minutes": i*3, "notes": "DEMO sample daily production.", "entered_by": actor})

        # A tiny valid PNG preview is attached to each seeded quality inspection.
        image_bytes = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/pV8AAAAASUVORK5CYII=")
        for i, (factory, department, product) in enumerate(zip(factories, departments, products), start=1):
            status = [QualityInspection.Status.PASS, QualityInspection.Status.PENDING, QualityInspection.Status.FAIL, QualityInspection.Status.PASS, QualityInspection.Status.PENDING, QualityInspection.Status.FAIL, QualityInspection.Status.PASS, QualityInspection.Status.PENDING, QualityInspection.Status.PASS, QualityInspection.Status.FAIL][i-1]
            QualityInspection.objects.get_or_create(factory=factory, product_reference=f"DEMO-QA-{i:03d}", defaults={"department": department, "inspected_by": actor, "image": ContentFile(image_bytes, name=f"demo-inspection-{i:03d}.png"), "status": status, "defect_label": "DEMO surface mark" if status == QualityInspection.Status.FAIL else "", "notes": "DEMO placeholder image and sample review only; no AI prediction."})
            meter = f"DEMO-METER-{i:03d}"
            for day_offset, kwh in ((1, Decimal(1000+i*100)), (0, Decimal(1030+i*100))):
                reading_at = timezone.make_aware(datetime.combine(today-timedelta(days=day_offset), time(9, 0)))
                EnergyReading.objects.get_or_create(factory=factory, meter_reference=meter, reading_at=reading_at, defaults={"department": department, "recorded_by": actor, "meter_kwh": kwh, "notes": "DEMO sample meter reading."})

        machine_statuses = [Machine.Status.OPERATIONAL, Machine.Status.MAINTENANCE, Machine.Status.BREAKDOWN, Machine.Status.OPERATIONAL, Machine.Status.INACTIVE, Machine.Status.OPERATIONAL, Machine.Status.MAINTENANCE, Machine.Status.OPERATIONAL, Machine.Status.BREAKDOWN, Machine.Status.OPERATIONAL]
        for i, (factory, department, unit) in enumerate(zip(factories, departments, units), start=1):
            machine, _ = Machine.objects.get_or_create(factory=factory, asset_tag=f"DEMO-MACH-{i:03d}", defaults={"department": department, "production_unit": unit, "name": ["CNC Machining Centre", "Hydraulic Press", "Conveyor Drive Motor", "Injection Moulding Machine", "Industrial Compressor", "Packaging Line Robot", "Laser Cutter", "Assembly Torque Station", "Cooling Pump", "Palletiser"][i-1], "manufacturer": "DEMO Equipment Co.", "model_number": f"DM-{i:04d}", "serial_number": f"DEMO-SN-{i:05d}", "status": machine_statuses[i-1]})
            kind = MaintenanceRecord.Kind.BREAKDOWN if i in (3, 9) else MaintenanceRecord.Kind.SCHEDULED
            record_status = MaintenanceRecord.Status.COMPLETED if i in (1, 3, 5) else MaintenanceRecord.Status.OPEN
            MaintenanceRecord.objects.get_or_create(machine=machine, summary=f"DEMO maintenance event {i:03d}", defaults={"kind": kind, "status": record_status, "scheduled_for": today+timedelta(days=i) if kind == MaintenanceRecord.Kind.SCHEDULED else None, "details": "DEMO sample maintenance or breakdown record.", "resolution": "DEMO sample service completed." if record_status == MaintenanceRecord.Status.COMPLETED else "", "reported_by": actor, "completed_at": now if record_status == MaintenanceRecord.Status.COMPLETED else None})
            SafetyIncident.objects.get_or_create(factory=factory, summary=f"DEMO safety incident {i:03d}", defaults={"department": department, "incident_date": today-timedelta(days=i), "details": ["DEMO near miss: material handling route observation.", "DEMO safety observation: machine guard inspection.", "DEMO incident: minor spill contained by team.", "DEMO observation: PPE compliance follow-up.", "DEMO near miss: forklift and pedestrian route review.", "DEMO observation: emergency exit access check.", "DEMO incident: first-aid response recorded.", "DEMO observation: lockout procedure review.", "DEMO near miss: elevated storage inspection.", "DEMO observation: workstation cable trip hazard."][i-1], "severity": [SafetyIncident.Severity.LOW, SafetyIncident.Severity.MODERATE, SafetyIncident.Severity.HIGH, SafetyIncident.Severity.LOW, SafetyIncident.Severity.MODERATE, SafetyIncident.Severity.LOW, SafetyIncident.Severity.HIGH, SafetyIncident.Severity.MODERATE, SafetyIncident.Severity.CRITICAL, SafetyIncident.Severity.LOW][i-1], "status": SafetyIncident.Status.RESOLVED if i in (1, 2, 4) else SafetyIncident.Status.INVESTIGATING, "reported_by": actor})

        for i, factory in enumerate(factories, start=1):
            item, _ = InventoryItem.objects.get_or_create(factory=factory, sku=f"DEMO-MAT-{i:03d}", defaults={"name": f"DEMO Industrial Material {i:02d}", "kind": InventoryItem.Kind.RAW if i % 2 else InventoryItem.Kind.FINISHED, "unit_of_measure": "kg", "minimum_stock": Decimal("25.000"), "standard_cost": Decimal(20+i), "is_active": True})
            warehouse, _ = Warehouse.objects.get_or_create(factory=factory, code=f"DEMO-WH-{i:02d}", defaults={"name": f"DEMO Warehouse {i:02d}", "location": f"Demo Zone {i:02d}"})
            supplier, _ = Supplier.objects.get_or_create(factory=factory, name=f"DEMO Supplier {i:02d}", defaults={"contact_name": f"Demo Contact {i:02d}", "email": f"demo.supplier{i:02d}@example.invalid", "phone": f"+910000001{i:02d}", "address": "Sample supplier record."})
            opening_quantity = Decimal("15.000") if i % 2 else Decimal("80.000")
            StockMovement.objects.get_or_create(item=item, warehouse=warehouse, kind=StockMovement.Kind.RECEIPT, reference=f"DEMO-OPEN-{i:03d}", defaults={"quantity": opening_quantity, "unit_cost": item.standard_cost, "created_by": actor, "notes": "DEMO opening stock."})
            StockMovement.objects.get_or_create(item=item, warehouse=warehouse, kind=StockMovement.Kind.CONSUMPTION, reference=f"DEMO-CONSUME-{i:03d}", defaults={"quantity": Decimal("2.000"), "unit_cost": item.standard_cost, "created_by": actor, "notes": "DEMO material consumption sample."})
            doc_kind = ProcurementDocument.Kind.REQUISITION if i % 2 else ProcurementDocument.Kind.ORDER
            doc_status = ProcurementDocument.Status.DRAFT if i % 2 else ProcurementDocument.Status.PART_RECEIVED
            document, _ = ProcurementDocument.objects.get_or_create(factory=factory, number=f"DEMO-PO-{i:03d}", defaults={"kind": doc_kind, "status": doc_status, "item": item, "warehouse": warehouse, "supplier": supplier, "quantity": Decimal("20.000"), "expected_date": today+timedelta(days=i), "created_by": actor, "notes": "DEMO procurement sample."})
            if document.kind == ProcurementDocument.Kind.ORDER:
                StockMovement.objects.get_or_create(item=item, warehouse=warehouse, kind=StockMovement.Kind.RECEIPT, procurement_document=document, reference=f"DEMO-GR-{i:03d}", defaults={"quantity": Decimal("5.000"), "unit_cost": item.standard_cost, "created_by": actor, "notes": "DEMO partial goods receipt."})

        if actor:
            for i in range(1, count+1):
                key = f"demo-audit-{i:02d}"
                UserActivity.objects.get_or_create(user=actor, action="demo_sample_activity", details={"seed_key": key, "description": f"DEMO sample audit event {i:02d}"}, defaults={"ip_address": "192.0.2.10"})
                AssistantQuery.objects.get_or_create(user=actor, question=f"DEMO sample question {i:02d}: summarize factory operations", defaults={"answer": f"Sample assistant answer {i:02d}; based on seeded demonstration records."})
                Notification.objects.get_or_create(user=actor, title=f"DEMO notification {i:02d}", defaults={"message": "Sample notification created by the demo data command.", "target_url": "/reports/notifications/"})

        self.stdout.write(self.style.SUCCESS(
            f"Demo data ready (count={count}): {count} companies and {count} factories, departments, units, employees, shifts, products, plans, and work orders; "
            f"{count*count} attendance and production entries; {count} leave/performance/quality/machine/maintenance/safety/inventory/warehouse/supplier/procurement records; {count*2} energy readings. "
            + ("Audit, assistant, and notification examples attached to " + actor.username + "." if actor else "No staff/superuser exists, so account-linked demo activity was skipped.")
        ))
