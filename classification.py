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


def classify_product(mfg_part_num: str, part_desc: str) -> tuple[str, str]:
    """
    Returns (category, product_type).

    category matches an existing curated template name (Dishwashers,
    Abrasives, ...) EXACTLY if it plausibly fits - otherwise a new,
    reasonable category name. A new category name automatically routes
    through extraction.py's uncurated-template fallback (the same path
    that got Lighting to 100% fill rate) - no separate wiring needed for
    genuinely novel categories to work.

    product_type is a short, human, searchable noun phrase (e.g.
    "dishwasher", "metal cut-off disc") - feeds directly into the search
    query, so it needs to read like something a person would type, not a
    formal taxonomy node name.
    """
    known_categories = list(CATEGORY_ATTRIBUTE_TEMPLATES.keys())
    prompt = f"""Classify this distributor catalog row.

Mfg_Part_Num: {mfg_part_num}
Part_Desc: {part_desc}

Known existing categories - use one of these EXACTLY (character-for-character)
if it plausibly fits: {known_categories}

If none of the known categories fit, propose a new, reasonably general
category name (2-3 words, e.g. "Lighting", "Fasteners", "Power Tools",
"Building Materials").

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
