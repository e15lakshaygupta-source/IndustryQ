import re

from django.core.exceptions import ValidationError


GSTIN_PATTERN = re.compile(r"^(?:0[1-9]|[1-2][0-9]|3[0-8]|97|99)[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
STATE_CODE_PATTERN = re.compile(r"^(?:0[1-9]|[1-2][0-9]|3[0-8]|97|99)$")
HSN_SAC_PATTERN = re.compile(r"^(?:\d{4}|\d{6}|\d{8})$")


def validate_state_code(value):
    if value and not STATE_CODE_PATTERN.fullmatch(value):
        raise ValidationError("Enter a valid two-digit Indian GST state code.")


def validate_gstin(value):
    if not value:
        return
    gstin = value.upper()
    if not GSTIN_PATTERN.fullmatch(gstin):
        raise ValidationError("Enter a structurally valid 15-character GSTIN.")
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    checksum = 0
    factor = 2
    for character in reversed(gstin[:-1]):
        product = alphabet.index(character) * factor
        checksum += product // 36 + product % 36
        factor = 1 if factor == 2 else 2
    expected = alphabet[(36 - checksum % 36) % 36]
    if gstin[-1] != expected:
        raise ValidationError("GSTIN checksum is invalid.")


def validate_hsn_sac(value):
    if value and not HSN_SAC_PATTERN.fullmatch(value):
        raise ValidationError("Enter an HSN/SAC code containing 4, 6, or 8 digits.")


def validate_purchase_bill_pdf(upload):
    if upload.size > 10 * 1024 * 1024:
        raise ValidationError("Supplier invoice PDF must be 10 MB or smaller.")
    if not upload.name.lower().endswith(".pdf"):
        raise ValidationError("Upload the supplier invoice as a PDF.")
    header = upload.read(1024)
    upload.seek(0)
    if b"%PDF-" not in header:
        raise ValidationError("The uploaded file is not a valid PDF.")
