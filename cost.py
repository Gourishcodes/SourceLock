"""
cost.py

Computes cost from REAL measured usage (usage_tracker.py), not assumed
averages - "here's what our test batch actually cost" is a much stronger
claim than "here's our estimate."

Pricing below is sourced and dated - re-verify before using in a final
pitch, prices in this space move fast (we hit three different stale-price/
stale-model surprises in one evening building this). Sourced 2026-08-18
from Google's and Tavily's public pricing pages via aggregated search.

Gemini Flash-Lite: $0.10 / 1M input tokens, $0.40 / 1M output tokens
  (gemini-2.5-flash-lite rate; gemini-flash-lite-latest is what we call in
  code, an alias whose underlying model may differ - treat this as the
  best available estimate, not an exact quote)
Tavily: $0.008/credit pay-as-you-go; advanced search (what we use, for
  richer content extraction) costs 2 credits/call, not 1. Volume plans
  drop this to $0.005/credit at higher usage tiers.
"""

from usage_tracker import get_usage

GEMINI_INPUT_PER_TOKEN = 0.10 / 1_000_000
GEMINI_OUTPUT_PER_TOKEN = 0.40 / 1_000_000
TAVILY_CREDITS_PER_ADVANCED_SEARCH = 2
TAVILY_COST_PER_CREDIT_PAYGO = 0.008
TAVILY_COST_PER_CREDIT_VOLUME = 0.005  # ~100K-credit tier

# Unilog's own stated volumes from the problem brief - use their numbers,
# not round ones, so the projection speaks directly to what they asked for.
CURRENT_MONTHLY_VOLUME = 150_000
TARGET_MONTHLY_VOLUME = 750_000


def compute_cost_report(num_skus_processed: int, tavily_rate: float = TAVILY_COST_PER_CREDIT_PAYGO) -> dict:
    """
    num_skus_processed: how many product rows this usage total covers -
    needed to get a genuine per-SKU average, not just a total spend.
    """
    usage = get_usage()

    gemini_cost = (usage["gemini_input_tokens"] * GEMINI_INPUT_PER_TOKEN +
                   usage["gemini_output_tokens"] * GEMINI_OUTPUT_PER_TOKEN)
    tavily_credits = usage["tavily_calls"] * TAVILY_CREDITS_PER_ADVANCED_SEARCH
    tavily_cost = tavily_credits * tavily_rate

    total_cost = gemini_cost + tavily_cost
    per_sku = total_cost / num_skus_processed if num_skus_processed else 0.0

    return {
        "num_skus": num_skus_processed,
        "gemini_calls": usage["gemini_calls"],
        "gemini_input_tokens": usage["gemini_input_tokens"],
        "gemini_output_tokens": usage["gemini_output_tokens"],
        "gemini_cost": gemini_cost,
        "tavily_calls": usage["tavily_calls"],
        "tavily_credits": tavily_credits,
        "tavily_cost": tavily_cost,
        "total_cost": total_cost,
        "cost_per_sku": per_sku,
        "projected_current_volume": per_sku * CURRENT_MONTHLY_VOLUME,
        "projected_target_volume": per_sku * TARGET_MONTHLY_VOLUME,
    }


def format_cost_report(report: dict) -> str:
    lines = [
        "=" * 70,
        "COST REPORT (measured from real API usage this run)",
        "=" * 70,
        f"SKUs processed:        {report['num_skus']}",
        f"Gemini calls:          {report['gemini_calls']} "
        f"({report['gemini_input_tokens']:,} input + {report['gemini_output_tokens']:,} output tokens)",
        f"Gemini cost:           ${report['gemini_cost']:.4f}",
        f"Tavily calls:          {report['tavily_calls']} ({report['tavily_credits']} credits)",
        f"Tavily cost:           ${report['tavily_cost']:.4f}",
        "-" * 70,
        f"Total cost:            ${report['total_cost']:.4f}",
        f"Cost per SKU:          ${report['cost_per_sku']:.5f}",
        "-" * 70,
        f"Projected at {CURRENT_MONTHLY_VOLUME:,}/month (current):  ${report['projected_current_volume']:,.2f}",
        f"Projected at {TARGET_MONTHLY_VOLUME:,}/month (target):   ${report['projected_target_volume']:,.2f}",
        "=" * 70,
        "Note: this run's per-SKU cost includes one-time brand->domain",
        "resolution calls that get CACHED across a batch - a batch with more",
        "SKUs per brand will show a lower true per-SKU average than this one.",
    ]
    return "\n".join(lines)
