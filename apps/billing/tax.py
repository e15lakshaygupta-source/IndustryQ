from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db.models import Q, Sum

from apps.billing.validators import validate_gstin, validate_hsn_sac, validate_state_code
from apps.operations.models import CreditDebitNote, GSTTaxRate


CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def state_code_for_invoice(invoice):
    factory = invoice.factory
    customer = invoice.customer
    seller_state = factory.state_code or factory.gstin[:2]
    place_of_supply = customer.state_code or customer.tax_registration_number[:2]
    try:
        validate_state_code(seller_state)
        validate_state_code(place_of_supply)
    except ValidationError as error:
        raise ValidationError(error.messages) from error
    return seller_state, place_of_supply


def effective_gst_rate(product, issue_date):
    if product and product.gst_tax_profile_id:
        if not product.gst_tax_profile.is_active:
            raise ValidationError(f"GST tax profile {product.gst_tax_profile} is inactive.")
        rate = GSTTaxRate.objects.filter(
            profile_id=product.gst_tax_profile_id,
            is_active=True,
            effective_from__lte=issue_date,
        ).filter(Q(effective_to__isnull=True) | Q(effective_to__gte=issue_date)).order_by("-effective_from").first()
        if not rate:
            raise ValidationError(
                f"No active GST rate for {product.gst_tax_profile} on {issue_date}."
            )
        return rate.rate
    if product:
        return product.tax_rate


def _rate_for_line(line, issue_date):
    product = line.get("product")
    if product:
        return effective_gst_rate(product, issue_date)
    try:
        rate = Decimal(str(line.get("tax_rate", "0") or "0"))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValidationError("Enter a valid GST rate for each manual line.") from error
    if rate < 0 or rate > 100:
        raise ValidationError("GST rates must be between 0 and 100%.")
    return rate


def calculate_gst_lines(lines, discount_amount, issue_date, invoice):
    gross_amounts = [money(line["quantity"] * line["unit_price"]) for line in lines]
    subtotal = sum(gross_amounts, ZERO)
    discount_amount = money(discount_amount)
    if discount_amount < 0 or discount_amount > subtotal:
        raise ValidationError("Discount cannot be negative or exceed the invoice subtotal.")

    seller_state, place_of_supply = state_code_for_invoice(invoice)
    rates = [_rate_for_line(line, issue_date) for line in lines]
    if any(rate > 0 for rate in rates) and (not seller_state or not place_of_supply):
        raise ValidationError("Set GST state codes for the factory and customer before calculating GST.")
    supply_type = (
        "intrastate" if seller_state and seller_state == place_of_supply
        else "interstate" if seller_state and place_of_supply
        else ""
    )

    remaining_discount = discount_amount
    calculated = []
    for index, (line, gross, rate) in enumerate(zip(lines, gross_amounts, rates)):
        if index == len(lines) - 1:
            line_discount = remaining_discount
        elif subtotal:
            line_discount = min(
                remaining_discount,
                money(discount_amount * gross / subtotal),
            )
        else:
            line_discount = ZERO
        remaining_discount -= line_discount
        taxable = gross - line_discount
        tax = money(taxable * rate / Decimal("100"))
        cgst = sgst = igst = ZERO
        if supply_type == "intrastate":
            cgst = money(tax / Decimal("2"))
            sgst = tax - cgst
        elif supply_type == "interstate":
            igst = tax
        product = line.get("product")
        hsn_sac_code = (product.hsn_sac_code if product else line.get("hsn_sac_code", "")).strip()
        try:
            validate_hsn_sac(hsn_sac_code)
        except ValidationError as error:
            raise ValidationError(f"{line['description']}: {error.messages[0]}") from error
        calculated.append({
            **line,
            "line_total": gross,
            "discount_amount": line_discount,
            "taxable_value": taxable,
            "tax_rate": rate,
            "hsn_sac_code": hsn_sac_code,
            "cgst_amount": cgst,
            "sgst_amount": sgst,
            "igst_amount": igst,
        })

    total_tax = sum((line["cgst_amount"] + line["sgst_amount"] + line["igst_amount"] for line in calculated), ZERO)
    weighted_rate = (
        (total_tax * Decimal("100") / (subtotal - discount_amount)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
        if subtotal > discount_amount else Decimal("0.000")
    )
    return {
        "lines": calculated,
        "subtotal": subtotal,
        "discount_amount": discount_amount,
        "taxable_value": subtotal - discount_amount,
        "cgst_amount": sum((line["cgst_amount"] for line in calculated), ZERO),
        "sgst_amount": sum((line["sgst_amount"] for line in calculated), ZERO),
        "igst_amount": sum((line["igst_amount"] for line in calculated), ZERO),
        "tax_amount": total_tax,
        "total": subtotal - discount_amount + total_tax,
        "tax_rate": weighted_rate,
        "supplier_gstin": invoice.factory.gstin.strip().upper(),
        "customer_gstin": invoice.customer.tax_registration_number.strip().upper(),
        "place_of_supply_state_code": place_of_supply,
        "gst_supply_type": supply_type,
    }


def calculate_purchase_gst_lines(lines, discount_amount, issue_date, bill):
    gross_amounts = [money(line["quantity"] * line["unit_price"]) for line in lines]
    subtotal = sum(gross_amounts, ZERO)
    discount_amount = money(discount_amount)
    if discount_amount < 0 or discount_amount > subtotal:
        raise ValidationError("Discount cannot be negative or exceed the bill subtotal.")

    supplier = bill.supplier
    supplier_gstin = supplier.gstin.strip().upper()
    buyer_gstin = bill.factory.gstin.strip().upper()
    supplier_state = supplier.state_code or supplier_gstin[:2]
    buyer_state = bill.factory.state_code or buyer_gstin[:2]
    rates = [_rate_for_line(line, issue_date) for line in lines]
    if any(rate > 0 for rate in rates):
        if not supplier_gstin:
            raise ValidationError("Enter the supplier GSTIN before recording GST input tax.")
        if not buyer_gstin:
            raise ValidationError("Set the factory GSTIN before recording GST input tax.")
        try:
            validate_gstin(supplier_gstin)
            validate_gstin(buyer_gstin)
            validate_state_code(supplier_state)
            validate_state_code(buyer_state)
        except ValidationError as error:
            raise ValidationError(error.messages) from error
        if supplier_gstin[:2] != supplier_state:
            raise ValidationError("Supplier GSTIN state must match its configured state code.")
        if buyer_gstin[:2] != buyer_state:
            raise ValidationError("Factory GSTIN state must match its configured state code.")
    supply_type = (
        "intrastate" if supplier_state and supplier_state == buyer_state
        else "interstate" if supplier_state and buyer_state
        else ""
    )

    remaining_discount = discount_amount
    calculated = []
    for index, (line, gross, rate) in enumerate(zip(lines, gross_amounts, rates)):
        if index == len(lines) - 1:
            line_discount = remaining_discount
        elif subtotal:
            line_discount = min(remaining_discount, money(discount_amount * gross / subtotal))
        else:
            line_discount = ZERO
        remaining_discount -= line_discount
        taxable = gross - line_discount
        tax = money(taxable * rate / Decimal("100"))
        cgst = sgst = igst = ZERO
        if supply_type == "intrastate":
            cgst = money(tax / Decimal("2"))
            sgst = tax - cgst
        elif supply_type == "interstate":
            igst = tax
        product = line.get("product")
        hsn_sac_code = (product.hsn_sac_code if product else line.get("hsn_sac_code", "")).strip()
        if rate > 0 and not hsn_sac_code:
            raise ValidationError(f"{line['description']}: add an HSN/SAC code to each taxable purchase line.")
        try:
            validate_hsn_sac(hsn_sac_code)
        except ValidationError as error:
            raise ValidationError(f"{line['description']}: {error.messages[0]}") from error
        calculated.append({
            **line,
            "line_total": gross,
            "discount_amount": line_discount,
            "taxable_value": taxable,
            "tax_rate": rate,
            "hsn_sac_code": hsn_sac_code,
            "cgst_amount": cgst,
            "sgst_amount": sgst,
            "igst_amount": igst,
        })

    total_tax = sum(
        (line["cgst_amount"] + line["sgst_amount"] + line["igst_amount"] for line in calculated),
        ZERO,
    )
    weighted_rate = (
        (total_tax * Decimal("100") / (subtotal - discount_amount)).quantize(
            Decimal("0.001"),
            rounding=ROUND_HALF_UP,
        )
        if subtotal > discount_amount else Decimal("0.000")
    )
    return {
        "lines": calculated,
        "subtotal": subtotal,
        "discount_amount": discount_amount,
        "taxable_value": subtotal - discount_amount,
        "cgst_amount": sum((line["cgst_amount"] for line in calculated), ZERO),
        "sgst_amount": sum((line["sgst_amount"] for line in calculated), ZERO),
        "igst_amount": sum((line["igst_amount"] for line in calculated), ZERO),
        "tax_amount": total_tax,
        "total": subtotal - discount_amount + total_tax,
        "tax_rate": weighted_rate,
        "supplier_gstin": supplier_gstin,
        "customer_gstin": buyer_gstin,
        "place_of_supply_state_code": buyer_state,
        "gst_supply_type": supply_type,
    }


def calculate_adjustment_lines(source_document, line_requests):
    calculated = []
    for source_line, quantity in line_requests:
        if source_line.document_id != source_document.pk:
            raise ValidationError("Each adjustment item must belong to the selected original invoice.")
        if quantity <= 0:
            raise ValidationError("Returned quantity must be greater than zero.")

        approved_lines = source_line.adjustment_lines.filter(
            note__status=CreditDebitNote.Status.APPROVED
        ).aggregate(
            quantity=Sum("quantity"),
            line_total=Sum("line_total"),
            discount_amount=Sum("discount_amount"),
            taxable_value=Sum("taxable_value"),
            cgst_amount=Sum("cgst_amount"),
            sgst_amount=Sum("sgst_amount"),
            igst_amount=Sum("igst_amount"),
        )
        approved_quantity = approved_lines["quantity"] or Decimal("0")
        remaining_quantity = source_line.quantity - approved_quantity
        if quantity > remaining_quantity:
            raise ValidationError(
                f"{source_line.description}: only {remaining_quantity} remains available to return."
            )

        values = {}
        for field in (
            "line_total",
            "discount_amount",
            "taxable_value",
            "cgst_amount",
            "sgst_amount",
            "igst_amount",
        ):
            original_value = getattr(source_line, field)
            previously_adjusted = approved_lines[field] or Decimal("0")
            if quantity == remaining_quantity:
                values[field] = original_value - previously_adjusted
            else:
                values[field] = money(original_value * quantity / source_line.quantity)

        calculated.append({
            "source_line": source_line,
            "description": source_line.description,
            "quantity": quantity,
            "unit_price": source_line.unit_price,
            "tax_rate": source_line.tax_rate,
            "hsn_sac_code": source_line.hsn_sac_code,
            **values,
        })

    if not calculated:
        raise ValidationError("Add at least one item to the return.")

    subtotal = sum((line["line_total"] for line in calculated), ZERO)
    discount_amount = sum((line["discount_amount"] for line in calculated), ZERO)
    taxable_value = sum((line["taxable_value"] for line in calculated), ZERO)
    cgst_amount = sum((line["cgst_amount"] for line in calculated), ZERO)
    sgst_amount = sum((line["sgst_amount"] for line in calculated), ZERO)
    igst_amount = sum((line["igst_amount"] for line in calculated), ZERO)
    tax_amount = cgst_amount + sgst_amount + igst_amount
    return {
        "lines": calculated,
        "subtotal": subtotal,
        "discount_amount": discount_amount,
        "taxable_value": taxable_value,
        "cgst_amount": cgst_amount,
        "sgst_amount": sgst_amount,
        "igst_amount": igst_amount,
        "tax_amount": tax_amount,
        "total": taxable_value + tax_amount,
    }


def validate_invoice_for_issue(invoice):
    lines = list(invoice.lines.all())
    taxable_lines = [line for line in lines if line.tax_rate > 0]
    company = invoice.factory.company
    customer = invoice.customer
    if not company.name.strip():
        raise ValidationError("Set the seller's legal company name before issuing the invoice.")
    if not (invoice.factory.address.strip() or company.address.strip()):
        raise ValidationError("Set the seller's registered address on the factory or company before issuing the invoice.")
    if not (invoice.factory.city.strip() or company.city.strip()):
        raise ValidationError("Set the seller's city on the factory or company before issuing the invoice.")
    if not (invoice.factory.state.strip() or company.state.strip()):
        raise ValidationError("Set the seller's state on the factory or company before issuing the invoice.")
    if not customer or not customer.name.strip():
        raise ValidationError("Select a customer with a verified legal name before issuing the invoice.")
    if not (customer.billing_address.strip() or customer.address.strip()):
        raise ValidationError("Set the customer's billing or registered address before issuing the invoice.")
    if not customer.state.strip():
        raise ValidationError("Set the customer's place-of-supply state before issuing the invoice.")

    seller_state = invoice.factory.state_code or invoice.factory.gstin[:2]
    customer_state = customer.state_code or customer.tax_registration_number[:2]
    if not seller_state:
        raise ValidationError("Set the seller's GST state code before issuing the invoice.")
    if not customer_state:
        raise ValidationError("Set the customer's GST state code before issuing the invoice.")
    try:
        validate_state_code(seller_state)
        validate_state_code(customer_state)
    except ValidationError as error:
        raise ValidationError(error.messages) from error
    if not invoice.place_of_supply_state_code:
        raise ValidationError("Set the customer's GST state code before issuing this invoice.")
    if customer_state != invoice.place_of_supply_state_code:
        raise ValidationError("Customer's GST state changed after draft creation; review and recreate this invoice.")

    if invoice.factory.gstin:
        try:
            validate_gstin(invoice.factory.gstin)
        except ValidationError as error:
            raise ValidationError(f"Factory GSTIN: {error.messages[0]}") from error
        if seller_state != invoice.factory.gstin[:2]:
            raise ValidationError("Factory GSTIN state must match its configured GST state code.")
    elif taxable_lines:
        raise ValidationError("Set the factory GSTIN before issuing a GST invoice.")

    if customer.tax_registration_number:
        try:
            validate_gstin(customer.tax_registration_number)
        except ValidationError as error:
            raise ValidationError(f"Customer GSTIN: {error.messages[0]}") from error
        if customer.tax_registration_number[:2] != customer_state:
            raise ValidationError("Customer GSTIN state must match the customer's place of supply.")

    if taxable_lines:
        if customer.tax_registration_number and customer.tax_registration_number[:2] != invoice.place_of_supply_state_code:
            raise ValidationError("Customer GSTIN state must match the invoice place of supply.")
    if any(not line.hsn_sac_code for line in taxable_lines):
        raise ValidationError("Add an HSN/SAC code to each taxable invoice line before issuing.")
