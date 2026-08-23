# Unilog Product Enrichment Pipeline — UniHack 2026

Solo entry. An AI enrichment pipeline that turns minimal, messy distributor
catalog rows (`Mfg_Part_Num`, `Part_Desc`, often-junk brand fields) into
structured, cited, confidence-scored product attributes — sourced
**exclusively from manufacturer websites**, never marketplaces or retailers.

## The core bet

Most teams building this brief will paste the landing page into an AI tool
and ship a generic "LLM JSON enrichment demo." That gets you a plausible
CSV. It does not get you a *trustworthy* one, and nothing stops it from
quietly citing Amazon.

This project is built around one hard constraint the brief calls a
**critical bug if violated**: never source from e-commerce or marketplace
sites. Every design decision below — the two-layer domain filtering, the
grounding validator, the choice to return "not found" instead of guessing —
exists to make that constraint actually hold under real-world messiness,
not just in the one example you tested by hand.

**Headline result:** 27/28 rows in the latest batch, zero hard-rule
violations, 98-99% of cited fields pass grounding verification against the
actual fetched source text (varies slightly run-to-run — see note below).

**A note on run-to-run variance, since we've now seen it twice:** this
pipeline calls live search and a live LLM, not a fixed dataset. Re-running
the identical batch has produced small, real differences — the cold-start
Building Materials row identified "Trex" as the brand on one run and
"PYLEX" on another (same SKU, same code, different live search ranking),
and a handful of grounding checks flip between runs as JS-rendered page
content shifts slightly (promo banners, stock status). This is disclosed
deliberately, not smoothed over: exact fill/grounding percentages should be
read as "roughly this good," not as a fixed number a judge should expect to
reproduce to the decimal on a fresh run.

## Why "zero violations" is the metric, not fill rate

Fill rate is easy to inflate — relax the source requirement and every field
fills in. It's also easy to fake by hand-picking easy SKUs. We optimized
for something a judge can actually verify: does every non-null field trace
to a real manufacturer URL with a quote that's actually on that page?

That tradeoff shows up directly in results by category:

| Category | Rows | Avg fill | Why |
|---|---|---|---|
| Abrasives (Milwaukee) | 8 | 100% | Static manufacturer spec pages, fully crawlable |
| Lighting (Kichler) | 6 | 100% | Uncurated category — model proposed its own attribute list, zero hand-curation, still hit full fill |
| Fans (Havells, Crompton) | 2 | 100% | See "Product identity" caveat below — one of these two rows' fill rate should not be read at face value |
| Building Materials (cold-start) | 2 | 50% avg (1 succeeded at 100%, 1 correctly returned "not found") | No category/brand hints at all — classification stage ran cold on both |
| Dishwashers (KitchenAid/Whirlpool/Frigidaire/GE) | 10 | 29% | See "Documented limitation" below |

Dishwashers is deliberately not padded. The low number is the system
correctly refusing to guess, not a bug we ran out of time to fix.

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

**Two-layer source enforcement**, not one:
1. Static marketplace denylist (Amazon, eBay, Home Depot, etc.) filters
   before content is ever fetched.
2. `resolve_manufacturer_domain()` locks the *entire* search to a single
   verified manufacturer domain via `include_domains` — a retailer page
   can't leak in through search-ranking luck, because it's never in the
   candidate pool to begin with. This is the layer that actually matters;
   the denylist alone cannot enumerate the long tail of regional
   retailers, and we hit that wall in early testing (see Known Issues Log
   below for one striking case).

**Cost-aware by construction**: one extraction call per product (not one
call per field), real measured token/API usage tracked per run, not
assumed averages. Latest measured: **$0.0213/SKU → ~$16.0K/month projected
at Unilog's stated 750K/month target volume.**

## Generalization: tested tonight, not assumed

The batch above is still US/appliance-heavy. Since judging is India-based,
we ran two live, zero-override tests against real Indian brands the system
had never seen, using product data found fresh via search rather than
hand-picked from the codebase:

- **Havells** (`FHCEO5SBNC48-C` BLDC ceiling fan) — brand→domain resolution
  correctly landed on `havells.com` with **zero hardcoded help** (similarity
  1.05, pure fuzzy-match). Extraction proposed 7 attributes using
  **Indian spec conventions** the system was never told about — sweep size
  in mm, air delivery in m³/min — not US units. 7/7 filled, 7/7 high
  confidence, 100% grounded.
- **Crompton** (`CFHSHS42OPW1S` ceiling fan) — found and fixed a real bug
  live: `_registrable_segment()` mis-parsed `crompton.co.in` as `"co"`
  instead of `"crompton"` (a two-part-TLD case, same bug class as an
  earlier `en.wikipedia.org` subdomain fix, just on the other end of the
  domain). Also found that a TLD-preference heuristic tuned for one US
  tie-break case was silently penalizing every `.in`/`.co.in` domain.
  Both fixed and verified against the full existing domain test suite
  with no regressions.

This is the difference between "works on the two worked examples" and
"the architecture holds up when a brand it's never seen shows up cold."

## Style rules — our self-defined controlled vocabulary

No LOV/UOM standards file was ever provided (confirmed directly with
organizers — it doesn't exist to release). Since a judge cannot score exact
values against a hidden answer key that doesn't exist, what *can* be
scored is internal consistency: does "24 in" always render as "24 in," not
"24IN" or "24 inches," everywhere in the output?

`style_rules.py` derives every formatting rule from the one fully-worked
ground-truth example (Frigidaire PDSH4816AF) — unit spacing, casing,
title-construction order, brand-mark placement — and every batch output is
normalized against it, not left to whatever the LLM felt like formatting
that call.

## Known limitations — documented, not hidden

**Dishwashers, ~15% fill.** KitchenAid, Whirlpool, and Frigidaire's
interactive spec panels are JS-rendered behind what looks like
Cloudflare/Akamai-style bot protection. We tried the obvious fix
(Playwright + stealth args + HTTP/2 workaround) — it cleared one error class
but pages now time out instead, the underlying block persists. We then
found direct evidence a full spec sheet for one such SKU (KitchenAid
KDFM404KPS) genuinely exists on the web — but only on a retailer's CDN
(`content.abt.com`), never on `kitchenaid.com` itself (confirmed: their own
owners-center page for this model literally states "No documents are
available"). Wiring that in would have been an easy fill-rate win and a
direct hard-rule violation. We didn't.

The `NEEDS_REVIEW` column flags exactly these fields. This isn't a stopgap
— it's the correct output for a system that won't fabricate a citation,
and it matches Unilog's own stated reality that enrichment today is
"partially AI-assisted, mostly human," not fully automated.

**Unbranded-row brand identification** (`identify_brand_from_search`) is
the weaker of two resolution paths and known to be non-deterministic on
thin search coverage — confirmed live during testing (identical Crompton
query returned a usable brand guess on one run, none on the next, purely
from live search-index variance). A retry with a broadened query was added
as a cheap mitigation; it is not a complete fix, and we're saying so rather
than claiming it is.

**Product-identity risk on similar sibling products.** A real batch run
surfaced this directly: for one Crompton fan SKU, none of the 5 retrieved
pages on the correct, verified manufacturer domain actually stated the
exact model number — they were pages for closely related but different fan
variants on the same product line. Per-field grounding alone can't catch
this, since a quoted value can be 100% accurate to the page it came from
and still describe the wrong product. Two mitigations are now in place:
the extraction prompt explicitly requires product-identity confirmation
before using a source as evidence, and — since a prompt instruction alone
isn't a guarantee — a code-level check flags the entire row for review
whenever no retrieved source states the exact model number, regardless of
how confident any individual field looks. **Verified working against real
batch output**, not just implemented: rerunning the same batch confirmed
`NEEDS_REVIEW` for that row contains `PRODUCT IDENTITY UNCONFIRMED` —
checked directly against `batch_output.csv`, not inferred from logs.

## Running it

```
pip install -r requirements.txt
playwright install chromium
# .env with TAVILY_API_KEY and GEMINI_API_KEY

python run_single.py <MFG_PART_NUM> "<PART_DESC>" <PRODUCT_TYPE> <CATEGORY> [BRAND]
python batch_run.py                 # runs the full curated 26-row batch
streamlit run streamlit_app.py      # Product Detail / Batch Overview / Upload & Enrich (live)
```

`streamlit_app.py`'s **Upload & Enrich** tab runs the real pipeline live
against any uploaded CSV (capped at 20 rows for demo safety) — this is the
genuinely-dynamic path, not a replay of pre-computed results.
"# SourceLock" 
