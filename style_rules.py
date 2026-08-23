"""
style_rules.py

Our self-defined "controlled vocabulary" - since Unilog gave us no LOV/UOM
standards file, these rules ARE our standard. Every rule here is derived
from comparing the two worked examples (Frigidaire PDSH4816AF, Whirlpool
WDTS7024RZ), not invented. Where the two examples disagreed, that's noted
explicitly rather than papered over.
"""

from typing import Optional
import re


# ---------------------------------------------------------------------------
# Brand normalization
# ---------------------------------------------------------------------------
# Placeholder strings found in the real E1_Brand / Unilog_Brand / DIB_Brand
# columns that all mean "no brand data", not "the brand is literally this
# string". Checked against the full 1000-row input file, not just the
# worked examples - "COMMODITY - UNBRANDED" only showed up there.
NULL_BRAND_MARKERS = {
    "-- unbranded --",
    "-- no unilog brand --",
    "-- no dib brand --",
    "commodity - unbranded",
    "-",
    "",
}


def clean_brand(value: Optional[str]) -> Optional[str]:
    """
    Returns None if the value is a known placeholder/junk marker,
    otherwise returns the stripped brand string.

    We compare lowercased so we don't miss casing variants like
    "-- Unbranded --" vs "-- unbranded --".
    """
    if value is None:
        return None
    stripped = value.strip()
    if stripped.lower() in NULL_BRAND_MARKERS:
        return None
    return stripped


# ---------------------------------------------------------------------------
# Unit formatting
# ---------------------------------------------------------------------------
# IMPORTANT: this is field-context-dependent, not one universal rule.
# LONG_DESC1 / attribute values -> spaced, normal case: "120 V", "50-1/4 in"
# INVOICE_DESC                  -> no-space, upper case: "120V", "50-1/4IN"
# Confirmed identical across both worked examples for both styles.

def format_unit_spaced(value: str, uom: str) -> str:
    """Standard style, used everywhere except INVOICE_DESC."""
    return f"{value} {uom}"


def format_unit_compact(value: str, uom: str) -> str:
    """INVOICE_DESC style: no space, UOM upper-cased, value untouched."""
    return f"{value}{uom.upper()}"


# ---------------------------------------------------------------------------
# Brand/trademark symbols
# ---------------------------------------------------------------------------
# Both examples show BRAND_NAME with a trailing (R) symbol: "FRIGIDAIRE(R)",
# "Whirlpool(R)". We apply this in generated title fields (SHORT_DESC etc.)
# but do NOT guess (TM) symbols on individual features - that requires
# knowing which feature names are actually trademarked, which we can't
# verify reliably from scraped text. Lower-confidence area, flagged not solved.
REGISTERED_MARK = "\u00ae"  # (R)


def brand_with_mark(brand: str) -> str:
    return f"{brand}{REGISTERED_MARK}"


# ---------------------------------------------------------------------------
# Filename conventions
# ---------------------------------------------------------------------------
# The two worked examples DISAGREE on brand casing in filenames:
#   FRIGIDAIRE_PDSH4816AF.jpg          (all caps)
#   Whirlpool_WDTS7024RZ.jpg           (title case)
# Ground truth is not internally consistent here across just 2 rows, so we
# are not trying to match it exactly. We pick ONE convention and apply it
# everywhere: brand token upper-cased, spaces stripped from MPN.

def spec_sheet_filename(brand: str, mpn: str) -> str:
    brand_token = brand.upper().replace(" ", "_")
    return f"{brand_token}_{mpn}_Specification_Sheet.pdf"


def product_image_filename(brand: str, mpn: str, index: Optional[int] = None) -> str:
    brand_token = brand.upper().replace(" ", "_")
    suffix = "" if index is None else f"_{index}"
    return f"{brand_token}_{mpn}{suffix}.jpg"


# ---------------------------------------------------------------------------
# Title/description formulas
# ---------------------------------------------------------------------------
# SHORT_DESC formula, confirmed identical structure across both rows:
#   {BRAND(R)} {Series} {MPN} {ProductType}[ With {Feature}], {attr1}, {attr2}, ...
def build_short_desc(brand: str, series: str, mpn: str, product_type: str,
                      with_feature: Optional[str], key_attrs: list[str]) -> str:
    # Real bug this fixes: when series is empty (confirmed case - KDFM404KPS
    # has no stated series), the old f-string still inserted a space for it,
    # producing "KitchenAid(R)  KDFM404KPS Dishwashers" - a visible double
    # space right in the demo's own output. Filtering out empty tokens
    # before joining avoids this regardless of which field is missing.
    tokens = [brand_with_mark(brand), series, mpn, product_type]
    core = " ".join(t for t in tokens if t)
    if with_feature:
        core += f" With {with_feature}"
    if key_attrs:
        core += ", " + ", ".join(key_attrs)
    return core


# RETAIL_DESC formula: same attr list as SHORT_DESC, but drops brand + MPN.
def build_retail_desc(series: str, product_type: str, key_attrs: list[str]) -> str:
    # Same fix as build_short_desc above - empty series was leaving a
    # visible leading space (" Dishwashers, Stainless Steel...").
    tokens = [series, product_type]
    core = " ".join(t for t in tokens if t)
    if key_attrs:
        core += ", " + ", ".join(key_attrs)
    return core


# ---------------------------------------------------------------------------
# Dishwasher attribute template
# ---------------------------------------------------------------------------
# Identical 15 labels, identical order, in BOTH worked examples. This is our
# pilot-category slot-filling template: the model fills these slots (null if
# not found in retrieved content), it does not freely invent attribute names.
#
# expects_uom=True  -> scalar value, split into ATTRIBUTE_VALUE + ATTRIBUTE_UOM
# expects_uom=False -> compound/descriptive value, everything goes in VALUE,
#                       UOM column stays blank (confirmed via Size,
#                       Minimum/Maximum Height which are multi-part strings)
DISHWASHER_ATTRIBUTE_TEMPLATE = [
    {"label": "Series",                "expects_uom": False},
    {"label": "Model",                 "expects_uom": False},
    {"label": "Number of Wash Cycles", "expects_uom": False},
    {"label": "Voltage Rating",        "expects_uom": True},
    {"label": "Amperage Rating",       "expects_uom": True},
    {"label": "Mounting Type",         "expects_uom": False},
    {"label": "Plug Type",             "expects_uom": False},
    {"label": "Size",                  "expects_uom": False},
    {"label": "Depth With Door Open",  "expects_uom": True},
    {"label": "Minimum Height",        "expects_uom": False},
    {"label": "Maximum Height",        "expects_uom": False},
    {"label": "Sound Level",           "expects_uom": True},
    {"label": "Material",              "expects_uom": False},
    {"label": "Color",                 "expects_uom": False},
    {"label": "Additional Information","expects_uom": False},
]


# Abrasives / cut-off wheels template - derived directly from the real
# labeled spec structure on Milwaukee's own product page (49-94-0013), not
# invented. Same principle as the dishwasher template: use the manufacturer's
# own field structure as our schema, don't guess at what attributes matter.
ABRASIVE_ATTRIBUTE_TEMPLATE = [
    {"label": "Application",          "expects_uom": False},
    {"label": "Arbor Design",         "expects_uom": False},
    {"label": "Arbor Hole Diameter",  "expects_uom": True},
    {"label": "Diameter",             "expects_uom": True},
    {"label": "Thickness",            "expects_uom": True},
    {"label": "Material Application", "expects_uom": False},
    {"label": "Max RPM",              "expects_uom": True},
    {"label": "Weight",               "expects_uom": True},
    {"label": "Pack Quantity",        "expects_uom": False},
    {"label": "Performance Tier",     "expects_uom": False},
    {"label": "Reinforcement Sheets", "expects_uom": False},
    {"label": "Shape",                "expects_uom": False},
    {"label": "Type",                 "expects_uom": False},
    {"label": "Warranty",             "expects_uom": False},
]

# Registry - add a curated template here for any category worth demoing
# well. Categories without a curated template fall back to a generic
# LLM-proposed attribute list in extraction.py, flagged as lower-confidence
# since it's not grounded in an observed real schema the way these are.
CATEGORY_ATTRIBUTE_TEMPLATES = {
    "Dishwashers": DISHWASHER_ATTRIBUTE_TEMPLATE,
    "Abrasives": ABRASIVE_ATTRIBUTE_TEMPLATE,
}


# ---------------------------------------------------------------------------
# Dishwasher key-attribute selection for SHORT_DESC / RETAIL_DESC
# ---------------------------------------------------------------------------
# Derived from comparing BOTH worked examples, not invented:
#   Frigidaire SHORT_DESC key attrs: "Leg Mounting, 5-Wash Cycle, Stainless Steel"
#     -> Mounting Type, Wash Cycle (hyphenated singular "N-Wash Cycle", NOT
#        the "N Wash Cycles" plural form used in LONG_DESC1), Material
#   Whirlpool SHORT_DESC key attrs: "Built-in Mounting, Stainless Steel, Stainless Steel"
#     -> Mounting Type, Material, Color (Wash Cycle was blank in this row's
#        attribute table, so it's correctly absent here - confirms the rule
#        is "include if present", not "always all four")
# Combined: order is Mounting Type, Wash Cycle, Material, Color - filtered
# to whichever are actually non-null for a given product, in that order.
# ONLY validated for Dishwashers - do not apply this formula to other
# categories, there's no evidence it generalizes.

def select_dishwasher_key_attrs(attributes: list[dict]) -> list[str]:
    by_label = {a["label"]: a for a in attributes}
    key_attrs = []

    mounting = by_label.get("Mounting Type")
    if mounting and mounting.get("value"):
        key_attrs.append(f"{mounting['value']} Mounting")

    wash_cycles = by_label.get("Number of Wash Cycles")
    if wash_cycles and wash_cycles.get("value"):
        key_attrs.append(f"{wash_cycles['value']}-Wash Cycle")

    material = by_label.get("Material")
    if material and material.get("value"):
        key_attrs.append(material["value"])

    color = by_label.get("Color")
    if color and color.get("value"):
        key_attrs.append(color["value"])

    return key_attrs


# ---------------------------------------------------------------------------
# Whitespace-artifact cleanup
# ---------------------------------------------------------------------------
# Real bug this fixes: Milwaukee's "Arbor Design" attribute came back as
# "Non- Threaded" - a stray space after the hyphen, straight from the raw
# scraped page text. Standard hyphenated compounds ("Non-Threaded") don't
# have spaces on either side; this is a scraping artifact, not intentional
# punctuation. The asymmetric pattern (space on exactly one side of a
# hyphen between two word characters) is the signal - a genuine em-dash or
# separator usually has spaces on BOTH sides or NEITHER, not one.

_HYPHEN_SPACE_AFTER = re.compile(r'(\w)-\s+(\w)')
_HYPHEN_SPACE_BEFORE = re.compile(r'(\w)\s+-(\w)')
_MULTI_SPACE = re.compile(r' {2,}')


def clean_value(value: Optional[str]) -> Optional[str]:
    """
    Cleanup pass for any extracted string value before it goes to final
    output - fixes whitespace artifacts from raw scraped text. Safe to
    call on any string value; returns None unchanged (nothing to clean).
    """
    if value is None:
        return None
    cleaned = _HYPHEN_SPACE_AFTER.sub(r'\1-\2', value)
    cleaned = _HYPHEN_SPACE_BEFORE.sub(r'\1-\2', cleaned)
    cleaned = _MULTI_SPACE.sub(' ', cleaned)
    return cleaned.strip()


def audit_extraction(attributes: list[dict]) -> list[str]:
    """
    Reports which attribute labels had a whitespace artifact BEFORE
    cleanup - for a visible "we caught and fixed N issues" stat, not just
    silently correcting things with no record of what happened.
    """
    flagged = []
    for a in attributes:
        value = a.get("value")
        if value and clean_value(value) != value:
            flagged.append(a["label"])
    return flagged
