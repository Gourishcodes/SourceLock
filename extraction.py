"""
extraction.py

One Gemini call per product. The model receives ONLY the pre-filtered,
already-fetched Tavily content (never raw web access itself) and must fill
the category attribute template, never invent new attribute names.

Confidence is not a free-form self-rating - the prompt gives a strict
rubric (below) so "high" specifically means "this exact value appears in
the provided text", not a vibe. We also cross-check in validation.py later:
a field claiming high confidence with no source_snippet is a contradiction
we can catch in code.
"""

import os
import json
from typing import Optional, Literal
from pydantic import BaseModel, Field
from google import genai
import usage_tracker

from style_rules import CATEGORY_ATTRIBUTE_TEMPLATES


Confidence = Literal["high", "medium", "low", "none"]


class AttributeResult(BaseModel):
    label: str
    value: Optional[str] = None
    uom: Optional[str] = None
    source_url: Optional[str] = None
    source_snippet: Optional[str] = Field(
        default=None,
        description="The exact short span of source text that supports this value. "
                    "Required if confidence is 'high'."
    )
    confidence: Confidence


class ProductExtraction(BaseModel):
    brand: Optional[str] = None
    manufacturer_name: Optional[str] = None
    series: Optional[str] = None
    product_type: str
    mfr_url: Optional[str] = None
    with_feature: Optional[str] = None
    certifications: list[str] = Field(default_factory=list)
    classpath: Optional[str] = None
    attributes: list[AttributeResult]


CONFIDENCE_RUBRIC = """
Confidence rubric - apply strictly, this is not a vibe rating:
- "high":   the value appears verbatim or near-verbatim in the provided source text.
            source_snippet is REQUIRED for high confidence.
- "medium": the value is reasonably inferred/computed from the provided text
            (e.g. unit conversion, combining two stated facts) but not a direct quote.
- "low":    a plausible guess based on partial or ambiguous text.
- "none":   not found anywhere in the provided source text. value MUST be null.

CRITICAL - a common mistake to avoid: a field is only correctly filled if the
source presents THAT SPECIFIC labeled value for THAT SPECIFIC attribute
(e.g. a spec table row "Performance Tier: Metal Cut-Off", a clearly labeled
figure). General marketing text that is merely topically related (e.g. broad
durability/performance claims like "10% faster cutting") is NOT valid
evidence for a specific attribute like "Performance Tier", even if it sounds
relevant and even if no other candidate value exists. If the source's
structured spec data for a field isn't present, that field is null - do not
substitute nearby marketing prose for a missing labeled value.

NEVER use outside/general knowledge to fill a field. If the provided source
text does not contain the information, the field is null with confidence "none".
Do not guess based on what similar products usually have.
"""


def build_prompt(product_type: str, category: str, mfg_part_num: str, part_desc: str, sources: list[dict]) -> str:
    template = CATEGORY_ATTRIBUTE_TEMPLATES.get(category)
    if template:
        template_labels = [slot["label"] for slot in template]
        template_instruction = (
            f"You must fill exactly these attribute labels, in this order, no others:\n"
            f"{json.dumps(template_labels, indent=2)}"
        )
    else:
        # No curated template for this category - let the model propose
        # its own reasonable attribute list. Less reliable than a curated
        # template (not grounded in an observed real schema), so extraction
        # should be treated as lower-confidence for uncurated categories.
        template_instruction = (
            "There is no predefined attribute list for this category. Propose "
            "5-10 attribute labels that a real spec sheet for this kind of "
            "product would have (e.g. dimensions, materials, ratings, "
            "certifications) - only include ones you can actually find values "
            "for in the sources below, do not pad the list with null placeholders."
        )

    source_text = "\n\n".join(
        f"--- SOURCE ({s['url']}) ---\n{s['content'][:15000]}" for s in sources
    )
    return f"""You are extracting structured product data for a {product_type} (category: {category}).

Input row (from a distributor's messy catalog, may be incomplete or wrong - do not trust it over the sources):
  Mfg_Part_Num: {mfg_part_num}
  Part_Desc: {part_desc}

{template_instruction}

{CONFIDENCE_RUBRIC}

Every non-null attribute value AND every top-level field (brand, manufacturer_name,
series, mfr_url) must cite the specific source_url and source_snippet it came from.
The source_snippet must be a quote from the page's actual visible content -
NEVER quote the "--- SOURCE (url) ---" marker line itself. Those marker lines
are structural separators I inserted between sources, not part of any page's
real content - a value like a model number appearing only in a URL slug, not
in the page's visible text, is not grounded and should be null instead.

PRODUCT IDENTITY CHECK - do this before extracting anything, not after: a
source page is only valid evidence for THIS product if it is genuinely about
Mfg_Part_Num {mfg_part_num} specifically. Manufacturers commonly sell closely
related sibling variants under near-identical names (e.g. a "Star Rated" vs
a "Plus" vs a base version of the same fan or dishwasher line, or a single
spec sheet covering two color/finish variants of one model). A page that is
clearly about the same PRODUCT LINE but does not actually state this exact
model number anywhere is NOT valid evidence for this row - do not borrow its
specs on the assumption sibling variants are identical, they frequently are
not. If none of the provided sources actually confirm this exact model
number, treat every attribute as "none" confidence rather than extracting
plausible-looking values from a similar but unconfirmed product.

VARIANT-TABLE TRAP - a real, confirmed failure mode, distinct from the check
above: a single page CAN genuinely be the right product and still contain a
size/color/capacity SELECTOR TABLE listing multiple variants side by side
(e.g. a fan's sweep-size options: 900mm / 1050mm / 1200mm, each with its own
speed/airflow/power numbers). The exact model number appearing correctly in
the page URL does NOT mean every number on that page describes this specific
variant - it means the PAGE is right, not that every ROW in every table on
it is. Check whether the model number/SKU code itself encodes a
distinguishing spec (a common pattern: a size figure embedded in the code
itself, e.g. a fan model ending in "48" typically denotes a 48-inch/1200mm
sweep) and prefer the table row consistent with that over any other row that
merely appears first or most prominent on the page. If the source doesn't
let you tell which row/variant matches this exact SKU, that attribute is
"none" confidence, not a guess from an unconfirmed row.

CLASSPATH IS DIFFERENT FROM EVERY OTHER FIELD - it does NOT need a source
citation. It is a retail-taxonomy classification judgment, not a fact you
look up on the page - a product page never states its own catalog breadcrumb.
Propose a plausible ">"-delimited category path from general to specific,
matching this real confirmed example's style exactly:
  "Appliances & Consumer Electronics>Kitchen Appliances>Built-In Dishwashers"
3-4 levels is typical. If you cannot confidently classify this product at
all, leave classpath null rather than guess something implausible.

Sources (this is the ONLY information you may use for every field EXCEPT
classpath - do not use prior knowledge about this product or brand):
{source_text}
"""


def extract(product_type: str, category: str, mfg_part_num: str, part_desc: str, sources: list[dict]) -> ProductExtraction:
    """
    Requires network access to generativelanguage.googleapis.com - will not
    run in this sandbox, run this on your own machine.
    """
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    prompt = build_prompt(product_type, category, mfg_part_num, part_desc, sources)

    resp = client.models.generate_content(
        # gemini-2.5-flash's free tier was cut to ~20 requests/day in a
        # Dec 2025 change - flash-lite has a separate, much larger quota
        # (~1000/day) and is more than capable for structured extraction.
        # Using the "-latest" alias, not a pinned version - we hit two
        # dead ends tonight (gemini-2.5-flash's free tier cut to ~20/day,
        # then gemini-2.5-flash-lite returning 404 "no longer available to
        # new users" despite being listed by the models.list() endpoint).
        # The alias always resolves to whatever's currently supported,
        # which should avoid a third stale pin.
        model="gemini-flash-lite-latest",
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "response_schema": ProductExtraction,
        },
    )
    usage_tracker.record_gemini_response(resp, prompt)
    return ProductExtraction.model_validate_json(resp.text)
