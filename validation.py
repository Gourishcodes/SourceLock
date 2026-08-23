"""
validation.py

Runs INLINE, right after extraction, while `sources` (the actual fetched
page content) is still in memory - not as a separate post-hoc pass on saved
JSON, because the JSON we save doesn't include full source content, only
the citation.

Real motivating case from batch testing: KDTS324SPS (KitchenAid) resolved
correctly to kitchenaid.ie, but the pages actually returned were about
stand mixers and food processors - nothing about dishwashers. Despite that,
the extraction filled in Material/Color/Additional Information anyway, each
with a source_url and source_snippet attached, looking well-cited. This is
exactly the failure mode a snippet-grounding check catches: a citation that
LOOKS like evidence but doesn't actually match what's in the cited source.

This does not require a second LLM call - pure string matching against
content we already have in memory, so it's free.
"""

import re
from extraction import ProductExtraction, AttributeResult


def _normalize(s: str) -> str:
    return re.sub(r'\s+', ' ', s.lower()).strip()


# ---------------------------------------------------------------------------
# Attribute-value type/unit sanity check
# ---------------------------------------------------------------------------
# Named failure case: a voltage attribute accidentally getting an amperage
# value. Grounding alone can't catch this - the citation could be entirely
# real and still be attached to the wrong field (e.g. the model correctly
# read "15A" off the page but filed it under Voltage Rating instead of
# Amperage Rating). Deliberately conservative: only flags a CONFIDENT
# mismatch between two DIFFERENT recognized unit families. Never flags a
# label or unit it doesn't recognize - uncurated categories can propose
# attribute names outside any fixed list, and a cautious miss is far
# cheaper than a false accusation that erodes trust in this whole check.
UNIT_FAMILIES = {
    "voltage": {"v", "volt", "volts"},
    "amperage": {"a", "amp", "amps", "ampere", "amperes"},
    "wattage": {"w", "watt", "watts"},
    "frequency": {"hz", "hertz"},
    "length": {"in", "inch", "inches", "ft", "feet", "mm", "cm", "m"},
    "weight": {"lb", "lbs", "pound", "pounds", "kg", "oz", "ounce", "ounces"},
    "sound": {"db", "dba", "decibel", "decibels"},
    "speed": {"rpm"},
}

LABEL_KEYWORD_TO_FAMILY = {
    "voltage": "voltage",
    "amperage": "amperage", "current": "amperage",
    "wattage": "wattage", "power": "wattage",
    "frequency": "frequency",
    "diameter": "length", "height": "length", "width": "length",
    "length": "length", "depth": "length", "thickness": "length",
    "size": "length", "arbor": "length",
    "weight": "weight",
    "sound": "sound", "noise": "sound",
    "rpm": "speed", "speed": "speed",
}


def check_unit_plausibility(label: str, uom: str) -> str | None:
    """Returns a warning string if label and uom look like different unit
    families (e.g. 'Voltage Rating' with uom 'A'), else None."""
    if not uom:
        return None
    label_lower = label.lower()
    uom_lower = uom.lower().strip()

    expected_family = next((fam for kw, fam in LABEL_KEYWORD_TO_FAMILY.items() if kw in label_lower), None)
    if expected_family is None:
        return None  # don't recognize this label - don't guess what it expects

    actual_family = next((fam for fam, units in UNIT_FAMILIES.items() if uom_lower in units), None)
    if actual_family is None:
        return None  # don't recognize this unit - don't flag on unfamiliar ground

    if actual_family != expected_family:
        return f"'{label}' expects a {expected_family} unit but got '{uom}' ({actual_family})"
    return None


def _find_source_content(url: str, sources: list[dict]) -> str:
    for s in sources:
        if s["url"] == url:
            return s["content"]
    return ""


def _all_source_content(sources: list[dict]) -> str:
    return "\n".join(s["content"] for s in sources)


def is_grounded(snippet: str, source_url: str, sources: list[dict], fragment_threshold: float = 0.5) -> bool:
    """
    Checks whether `snippet` actually appears in the content of the source
    it claims to come from. Not a strict single substring check - real
    citations often combine text from two places on the same page (e.g.
    "Arbor Size:7/8 in; 7/8\" Arbor" - Options panel value + a marketing
    bullet, joined with a semicolon). So: split on common separators, and
    require at least `fragment_threshold` of the fragments to individually
    appear in the source content.

    Falls back to checking ALL fetched sources' content, not just the one
    matching source_url exactly, if the exact URL lookup misses. Real
    observed cause: the model can slightly garble a cited URL when several
    similar URLs are in context - one real case blended the %2B-encoding
    style of one URL with a different word ("wash" vs "max") from another,
    producing a citation URL that matched neither exactly, even though the
    actual snippet text was genuinely present in one of the real sources.
    Treating a URL mismatch alone as "not grounded" would flag correct,
    real citations as fabricated - the question that actually matters is
    whether the text was in what we fetched, not whether the model
    reproduced the exact URL string with full fidelity.
    """
    content = _find_source_content(source_url, sources)
    if not content:
        content = _all_source_content(sources)
    if not content:
        return False

    content_norm = _normalize(content)
    snippet_norm = _normalize(snippet)

    if snippet_norm in content_norm:
        return True

    fragments = [f.strip() for f in re.split(r'[;\n]', snippet_norm) if f.strip()]
    if not fragments:
        return False

    matched = sum(1 for f in fragments if f in content_norm)
    return (matched / len(fragments)) >= fragment_threshold


def validate_extraction(extraction: ProductExtraction, sources: list[dict]) -> dict:
    """
    Returns a report: {flagged: [labels], grounded_count, ungrounded_count,
    unit_mismatches: [strings]}. Also MUTATES extraction in place - any
    attribute whose citation fails grounding OR whose unit looks mismatched
    to its label gets downgraded to confidence="low" regardless of what the
    model originally claimed.
    """
    flagged = []
    grounded_count = 0
    unit_mismatches = []

    for attr in extraction.attributes:
        if attr.value is None:
            continue  # nothing to check

        mismatch = check_unit_plausibility(attr.label, attr.uom) if attr.uom else None
        if mismatch:
            unit_mismatches.append(mismatch)
            print(f"[validation] {mismatch} - downgrading confidence")
            attr.confidence = "low"
            if attr.label not in flagged:
                flagged.append(attr.label)
            continue  # a unit mismatch is disqualifying on its own - skip grounding check, already flagged

        if not attr.source_url or not attr.source_snippet:
            flagged.append(attr.label)
            attr.confidence = "low"
            continue
        if is_grounded(attr.source_snippet, attr.source_url, sources):
            grounded_count += 1
        else:
            flagged.append(attr.label)
            print(f"[validation] '{attr.label}' claims a citation that doesn't match its source - downgrading confidence")
            attr.confidence = "low"

    return {
        "flagged": flagged,
        "grounded_count": grounded_count,
        "ungrounded_count": len(flagged),
        "unit_mismatches": unit_mismatches,
    }
