# Unilog Product Enrichment Pipeline: UniHack 2026

Solo entry. An AI enrichment pipeline that turns minimal, messy distributor
catalog rows (`Mfg_Part_Num`, `Part_Desc`, often-junk brand fields) into
structured, cited, confidence-scored product attributes, sourced
exclusively from manufacturer websites, never marketplaces or retailers.

## Design principle

The brief treats sourcing from e-commerce or marketplace sites as a
critical bug. Every design decision in this pipeline exists to make that
constraint hold under real-world messiness, not just on a single hand-tested
example. That includes the two-layer domain filtering, the grounding
validator, and the decision to return "not found" rather than guess when a
source cannot be confirmed.

Headline result: 27 of 28 rows in the latest batch, zero hard-rule
violations, 98 to 99 percent of cited fields pass grounding verification
against the actual fetched source text. This varies slightly run to run;
see the note below.

A note on run-to-run variance. This pipeline calls live search and a live
LLM, not a fixed dataset. Re-running the identical batch has produced small,
real differences. The cold-start Building Materials row identified "Trex"
as the brand on one run and "PYLEX" on another, same SKU, same code,
different live search ranking. A handful of grounding checks also flip
between runs as JS-rendered page content shifts slightly, for example promo
banners or stock status. This is disclosed deliberately: exact fill and
grounding percentages should be read as directionally accurate, not as a
fixed number to reproduce exactly on a fresh run.

## Why zero violations is the metric, not fill rate

Fill rate is easy to inflate by relaxing the source requirement, and easy
to overstate by hand-picking easy SKUs. This project optimizes for
something a judge can actually verify: does every non-null field trace to a
real manufacturer URL with a quote that is actually on that page?

That tradeoff shows up directly in results by category:

| Category | Rows | Avg fill | Why |
|---|---|---|---|
| Abrasives (Milwaukee) | 8 | 100% | Static manufacturer spec pages, fully crawlable |
| Lighting (Kichler) | 6 | 100% | Uncurated category. Model proposed its own attribute list with zero hand-curation and still hit full fill |
| Fans (Havells, Crompton) | 2 | 100% | See the product identity caveat below; one of these two rows' fill rate should not be read at face value |
| Building Materials (cold-start) | 2 | 50% avg | One row succeeded at 100%, one correctly returned "not found." No category or brand hints at all; classification ran cold on both |
| Dishwashers (KitchenAid, Whirlpool, Frigidaire, GE) | 10 | 29% | See the documented limitation below |

Dishwashers is deliberately not padded. The low number reflects the system
correctly refusing to guess, not a gap that ran out of time to fix.

## Architecture

```
input row → classification (if category/type unknown)
          → domain resolution, LOCKED (brand → verified manufacturer domain)
          → search, re-locked to that domain only (include_domains)
          → thin-content detection → JS-render fallback (Playwright) if needed
          → single structured extraction call (Gemini, category template
            or model-proposed attribute list for uncurated categories)
          → grounding validation (snippet-vs-source string match, no 2nd LLM call)
          → style-rule normalization → schema-mapped CSV + per-row JSON
```

Source enforcement operates in two layers, not one:

1. A static marketplace denylist (Amazon, eBay, Home Depot, and similar)
   filters before content is ever fetched.
2. `resolve_manufacturer_domain()` locks the entire search to a single
   verified manufacturer domain via `include_domains`, so a retailer page
   cannot leak in through search-ranking luck; it is never in the candidate
   pool to begin with. This is the layer that matters most. The denylist
   alone cannot enumerate the long tail of regional retailers, and early
   testing surfaced that gap directly (see the Known Issues Log below for
   one striking case).

The pipeline is cost-aware by construction: one extraction call per
product, not one call per field, with real measured token and API usage
tracked per run rather than assumed averages. Latest measured cost is
$0.0213 per SKU, projecting to approximately $16.0K per month at Unilog's
stated 750K per month target volume.

## Generalization: tested, not assumed

The batch above is still US and appliance-heavy. Since judging is
India-based, two live, zero-override tests were run against real Indian
brands the system had never seen, using product data found fresh via
search rather than hand-picked from the codebase.

- Havells (`FHCEO5SBNC48-C`, BLDC ceiling fan): brand-to-domain resolution
  correctly landed on `havells.com` with zero hardcoded help (similarity
  1.05, pure fuzzy match). Extraction proposed 7 attributes using Indian
  spec conventions the system was never told about, including sweep size
  in mm and air delivery in m³/min rather than US units. 7 of 7 filled, 7
  of 7 high confidence, 100 percent grounded.
- Crompton (`CFHSHS42OPW1S`, ceiling fan): this test surfaced and fixed a
  real bug live. `_registrable_segment()` mis-parsed `crompton.co.in` as
  `"co"` instead of `"crompton"`, a two-part-TLD case in the same bug class
  as an earlier `en.wikipedia.org` subdomain fix, on the other end of the
  domain. It also surfaced that a TLD-preference heuristic tuned for one US
  tie-break case was silently penalizing every `.in` and `.co.in` domain.
  Both issues were fixed and verified against the full existing domain
  test suite with no regressions.

This distinguishes "works on the two worked examples" from "the
architecture holds up when a brand it has never seen shows up cold."

## Style rules: a self-defined controlled vocabulary

No LOV or UOM standards file was ever provided; this was confirmed
directly with organizers, and no such file exists to release. Since a
judge cannot score exact values against a hidden answer key that does not
exist, what can be scored is internal consistency: does "24 in" always
render as "24 in," never "24IN" or "24 inches," everywhere in the output?

`style_rules.py` derives every formatting rule from the one fully-worked
ground-truth example (Frigidaire PDSH4816AF): unit spacing, casing,
title-construction order, and brand-mark placement. Every batch output is
normalized against it rather than left to whatever the model produced on
that call.

## Known limitations

**Dishwashers, approximately 15 percent fill.** KitchenAid, Whirlpool, and
Frigidaire's interactive spec panels are JS-rendered behind what appears to
be Cloudflare or Akamai-style bot protection. The obvious fix, Playwright
with stealth arguments and an HTTP/2 workaround, cleared one error class
but pages now time out instead; the underlying block persists. Direct
evidence was found that a full spec sheet for one such SKU (KitchenAid
KDFM404KPS) genuinely exists on the web, but only on a retailer's CDN
(`content.abt.com`), never on `kitchenaid.com` itself; their own
owners-center page for this model states "No documents are available."
Wiring that source in would have been an easy fill-rate win and a direct
hard-rule violation, so it was not done.

The `NEEDS_REVIEW` column flags exactly these fields. This is the correct
output for a system that will not fabricate a citation, and it matches
Unilog's own stated reality that enrichment today is partially AI-assisted
and mostly human, not fully automated.

**Unbranded-row brand identification.** `identify_brand_from_search` is
the weaker of two resolution paths and is known to be non-deterministic on
thin search coverage. This was confirmed live during testing: an identical
Crompton query returned a usable brand guess on one run and none on the
next, purely from live search-index variance. A retry with a broadened
query was added as a cheap mitigation. It is not a complete fix, and this
is stated plainly rather than overstated.

**Product identity risk on similar sibling products.** A real batch run
surfaced this directly. For one Crompton fan SKU, none of the 5 retrieved
pages on the correct, verified manufacturer domain actually stated the
exact model number; they described closely related but different fan
variants on the same product line. Per-field grounding alone cannot catch
this, since a quoted value can be entirely accurate to the page it came
from and still describe the wrong product. Two mitigations are in place:
the extraction prompt requires explicit product-identity confirmation
before using a source as evidence, and, since a prompt instruction alone
is not a guarantee, a code-level check flags the entire row for review
whenever no retrieved source states the exact model number, regardless of
how confident any individual field appears. This was verified against real
batch output, not just implemented: re-running the same batch confirmed
`NEEDS_REVIEW` for that row contains `PRODUCT IDENTITY UNCONFIRMED`,
checked directly against `batch_output.csv`, not inferred from logs.

**Playwright on the deployed Streamlit Cloud instance.** The JS-render
fallback described above is verified working in local development runs.
It is not guaranteed on the deployed Streamlit Cloud instance, which lacks
the system-level browser dependencies Playwright requires in a
containerized free-tier environment. This is a constraint of the hosting
platform, not a pipeline design flaw. The static-fetch path and its
grounding and validation guarantees are unaffected either way.

## Running it

```
pip install -r requirements.txt
playwright install chromium
# .env with TAVILY_API_KEY and GEMINI_API_KEY

python run_single.py <MFG_PART_NUM> "<PART_DESC>" <PRODUCT_TYPE> <CATEGORY> [BRAND]
python batch_run.py                 # runs the full curated batch
streamlit run streamlit_app.py      # Product Detail / Batch Overview / Upload & Enrich (live)
```

The Upload & Enrich tab in `streamlit_app.py` runs the pipeline live
against any uploaded CSV, capped at 20 rows for demo safety. This is the
genuinely dynamic path, not a replay of pre-computed results.
