"""
classification.py

Stage 0 that was missing: given ONLY a messy input row (Mfg_Part_Num,
Part_Desc - no pre-assigned category, no product_type), classify what kind
of product this is. Needed for accepting a genuinely dynamic/uploaded
dataset - BATCH_ROWS' hand-labeled category field doesn't scale to "the
evaluator uploads their own file."

One cheap Gemini call, same cost tier as identify_brand_from_search in
retrieval.py - not a second full extraction call.
"""

import os
from pydantic import BaseModel
from google import genai
import usage_tracker
from style_rules import CATEGORY_ATTRIBUTE_TEMPLATES


class Classification(BaseModel):
    category: str
    product_type: str


def classify_product(mfg_part_num: str, part_desc: str, known_categories_this_run: set[str] | None = None) -> tuple[str, str]:
    """
    Returns (category, product_type).

    category matches an existing curated template name (Dishwashers,
    Abrasives, ...) EXACTLY if it plausibly fits - otherwise a new,
    reasonable category name. A new category name automatically routes
    through extraction.py's uncurated-template fallback (the same path
    that got Lighting to 100% fill rate) - no separate wiring needed for
    genuinely novel categories to work.

    known_categories_this_run: pass the SAME set object across every row in
    a batch (caller mutates it in place after each call - same pattern as
    domain_cache in retrieval.py). Without this, each row's classification
    call has zero memory of what earlier rows in the same batch were
    labeled, and can mint synonymous-but-different category strings for the
    same real-world category (e.g. "Fans" on one row, "Ceiling Fans" on the
    next unlabeled fan row) - fragmenting INTERNAL_Category grouping and the
    Batch Overview tab's by-category stats for no real reason. This is not
    a correctness issue for any single row, only a consistency issue across
    a batch, which is exactly why it needs to be threaded through the loop
    rather than fixed inside a single call.

    product_type is a short, human, searchable noun phrase (e.g.
    "dishwasher", "metal cut-off disc") - feeds directly into the search
    query, so it needs to read like something a person would type, not a
    formal taxonomy node name.
    """
    known_categories = list(CATEGORY_ATTRIBUTE_TEMPLATES.keys())
    dynamic_categories = sorted(known_categories_this_run) if known_categories_this_run else []

    dynamic_block = ""
    if dynamic_categories:
        dynamic_block = f"""
Categories already assigned to OTHER rows earlier in this same batch - these
are not a fixed taxonomy, just names this run has already used. Prefer
reusing one of these EXACTLY (character-for-character) over minting a new,
synonymous category name, if it plausibly fits: {dynamic_categories}
"""

    prompt = f"""Classify this distributor catalog row.

Mfg_Part_Num: {mfg_part_num}
Part_Desc: {part_desc}

Known existing categories - use one of these EXACTLY (character-for-character)
if it plausibly fits: {known_categories}
{dynamic_block}
If none of the above fit, propose a new, reasonably general category name
(2-3 words, e.g. "Lighting", "Fasteners", "Power Tools", "Building Materials").

Also give product_type - a short (2-4 word) human, searchable noun phrase
for the specific kind of item (e.g. "dishwasher", "metal cut-off disc",
"wall light fixture") - this feeds a web search query, so it should read
like something a person would actually type, not a formal category name.
"""
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    resp = client.models.generate_content(
        model="gemini-flash-lite-latest",
        contents=prompt,
        config={"response_mime_type": "application/json", "response_schema": Classification},
    )
    usage_tracker.record_gemini_response(resp, prompt)
    result = Classification.model_validate_json(resp.text)
    return result.category, result.product_type
