"""
run_single.py

Stage 2 checkpoint: one real, un-worked product end to end.
Run this on YOUR machine (not in Claude's sandbox - network there is
locked to a fixed allowlist that doesn't include Tavily/Gemini).

Usage:
    python run_single.py <MFG_PART_NUM> "<PART_DESC>" <PRODUCT_TYPE> <CATEGORY> [BRAND]

Examples:
    python run_single.py KDFM404KPS "KDFM404KPS Dishwasher SS" dishwasher Dishwashers
    python run_single.py "49-94-0013" "Milw 5in x.045in x7/8in Metal Cut Off Disc" "metal cut-off wheel" Abrasives Milwaukee

CATEGORY must match a key in style_rules.CATEGORY_ATTRIBUTE_TEMPLATES for the
curated-template path (currently: Dishwashers, Abrasives). Any other value
falls back to the model proposing its own attribute list - works, but lower
confidence since it's not grounded in an observed real schema.

BRAND is optional but matters a lot: pass it when you can (from E1_Brand
when it's not a placeholder, or parsed from Part_Desc) - it routes through
the reliable domain-resolution path instead of the weaker fallback scorer.
"""

import sys
import os
import json
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the SAME FOLDER as this script, regardless of which
# directory you happen to run `python run_single.py` from. Without this,
# load_dotenv() only checks the current working directory, which is a
# common source of "works on my machine, not from this folder" bugs.
#
# encoding="utf-8-sig" (not plain "utf-8") matters on Windows: classic
# PowerShell's `Set-Content -Encoding UTF8` writes a UTF-8 byte-order-mark
# at the start of the file. Plain utf-8 decoding leaves that BOM glued onto
# the first key name (TAVILY_API_KEY becomes "\ufeffTAVILY_API_KEY"), which
# fails the lookup silently. utf-8-sig strips a BOM if present and behaves
# like normal utf-8 if it's absent - safe either way.
load_dotenv(dotenv_path=Path(__file__).parent / ".env", encoding="utf-8-sig")

# Fail loud and clear here, not as a bare KeyError three files deep later.
missing = [k for k in ("TAVILY_API_KEY", "GEMINI_API_KEY") if not os.environ.get(k)]
if missing:
    print(f"Missing environment variable(s): {', '.join(missing)}")
    print(f"Expected a file named '.env' in: {Path(__file__).parent}")
    print("It should contain lines like:")
    print("    TAVILY_API_KEY=tvly-xxxxx")
    print("    GEMINI_API_KEY=AIzaxxxxx")
    print("(On Windows, creating a file literally named '.env' via File Explorer's")
    print(" 'New File' often silently fails or renames it - use the PowerShell")
    print(' command below instead of the GUI.)')
    sys.exit(1)

from retrieval import search_product_verified
from extraction import extract
from validation import validate_extraction


def run(mfg_part_num: str, part_desc: str, product_type: str, category: str, brand: str = None):
    query = f"{mfg_part_num} {product_type} specifications"
    print(f"[1/2] Searching (verified-domain mode): {query!r} (brand={brand!r}, category={category!r})")
    sources = search_product_verified(query, mfg_part_num, product_type=product_type, brand=brand, max_results=5)

    if not sources:
        print("No sources survived retrieval + domain filtering.")
        print("This is a valid outcome - better to report 'not found' than hallucinate.")
        return

    print(f"      Found {len(sources)} allowed source(s):")
    for s in sources:
        print(f"        - {s['url']}")

    print(f"[2/2] Extracting via Gemini...")
    result = extract(product_type, category, mfg_part_num, part_desc, sources)

    report = validate_extraction(result, sources)
    if report["ungrounded_count"]:
        print(f"  Validation: {report['ungrounded_count']} field(s) failed grounding check and were downgraded: {report['flagged']}")
    else:
        print(f"  Validation: all {report['grounded_count']} cited field(s) passed grounding check")

    print()
    print("=" * 70)
    print(f"Brand:             {result.brand}")
    print(f"Manufacturer:      {result.manufacturer_name}")
    print(f"Series:            {result.series}")
    print(f"MFR URL:           {result.mfr_url}")
    print(f"Certifications:    {result.certifications}")
    print("-" * 70)

    filled = 0
    high_conf = 0
    for attr in result.attributes:
        marker = {"high": "***", "medium": "** ", "low": "*  ", "none": "   "}[attr.confidence]
        val_display = f"{attr.value} {attr.uom or ''}".strip() if attr.value else "(null)"
        print(f"  [{marker}] {attr.label:24} {val_display}")
        if attr.source_url:
            print(f"           source: {attr.source_url}")
            print(f"           quote:  \"{attr.source_snippet}\"")
        if attr.value is not None:
            filled += 1
        if attr.confidence == "high":
            high_conf += 1

    print("-" * 70)
    print(f"Filled: {filled}/{len(result.attributes)}  |  High confidence: {high_conf}/{len(result.attributes)}")
    print("=" * 70)

    # Save raw JSON for later batch-stage comparison / debugging
    with open(f"output_{mfg_part_num}.json", "w") as f:
        json.dump(result.model_dump(), f, indent=2)
    print(f"\nFull result saved to output_{mfg_part_num}.json")

    from cost import compute_cost_report, format_cost_report
    print()
    print(format_cost_report(compute_cost_report(num_skus_processed=1)))


if __name__ == "__main__":
    if len(sys.argv) < 5:
        print('Usage: python run_single.py <MFG_PART_NUM> "<PART_DESC>" <PRODUCT_TYPE> <CATEGORY> [BRAND]')
        print('Example: python run_single.py KDFM404KPS "KDFM404KPS Dishwasher SS" dishwasher Dishwashers')
        sys.exit(1)
    brand = sys.argv[5] if len(sys.argv) > 5 else None
    run(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], brand=brand)
