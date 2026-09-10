"""
streamlit_app.py

Stage 4 UI, now with a real upload flow. Two modes:
  - Product Detail / Batch Overview: reads pre-computed batch_output/ -
    instant to load, safe to click around without burning quota.
  - Upload & Enrich: actually calls the live pipeline (real Tavily search,
    real Gemini extraction) on whatever CSV you upload - genuinely dynamic,
    not pre-baked, per the "accept a dynamic/uploaded dataset" requirement.

Run with:
    streamlit run streamlit_app.py
"""

import os
import io
import json
import csv
from pathlib import Path
from dotenv import load_dotenv
import streamlit as st

# Streamlit Cloud has no .env file (gitignored on purpose - never commit
# real keys). It has its own separate secrets store instead, filled in
# via the app dashboard (Settings -> Secrets), exposed to Python as
# st.secrets - NOT automatically merged into os.environ. Every other
# module in this pipeline (retrieval.py, extraction.py, classification.py)
# reads keys via os.environ[...], so we bridge the two here, once, at
# startup - "if it's not already set locally via .env, but IS available
# in Streamlit's secrets, copy it into os.environ" - this way the exact
# same os.environ.get(...) code works unchanged whether we're running on
# your laptop (.env) or on Streamlit Cloud (st.secrets).
for _key in ("TAVILY_API_KEY", "GEMINI_API_KEY"):
    if not os.environ.get(_key) and _key in st.secrets:
        os.environ[_key] = st.secrets[_key]

# ---------------------------------------------------------------------------
# Design: spec-sheet / blueprint aesthetic, not the generic cream+terracotta
# or near-black+neon AI-tell looks. Cool paper tones + monospace for
# technical values (part numbers, measurements genuinely need to align and
# scan, not just look "techy"). Confidence colors are FUNCTIONAL - they're
# the actual grounding signal, not decoration.
# ---------------------------------------------------------------------------
PALETTE = {
    "paper": "#F3F5F7",
    "ink": "#16232E",
    "blueprint": "#1B3A5C",
    "blueprint_light": "#3D6A94",
    "high": "#2D7D46",
    "medium": "#B8862B",
    "low": "#8B95A1",
    "none": "#C0442F",
    "border": "#D6DCE1",
}

st.markdown(f"""
<style>
    @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap');

    html, body, [class*="css"] {{
        font-family: 'IBM Plex Sans', sans-serif;
    }}
    .stApp {{
        background-color: {PALETTE['paper']};
    }}
    .mono {{
        font-family: 'IBM Plex Mono', monospace;
    }}
    .spec-row {{
        display: flex;
        align-items: center;
        padding: 10px 14px;
        border-bottom: 1px solid {PALETTE['border']};
        gap: 12px;
    }}
    .spec-label {{
        flex: 0 0 220px;
        color: {PALETTE['ink']};
        font-weight: 500;
        font-size: 0.92rem;
    }}
    .spec-value {{
        flex: 1;
        font-family: 'IBM Plex Mono', monospace;
        font-size: 0.95rem;
        color: {PALETTE['ink']};
    }}
    .conf-badge {{
        flex: 0 0 auto;
        padding: 3px 10px;
        border-radius: 3px;
        font-size: 0.75rem;
        font-weight: 600;
        font-family: 'IBM Plex Mono', monospace;
        color: white;
        text-transform: uppercase;
        letter-spacing: 0.03em;
    }}
    .header-card {{
        background-color: white;
        border: 1px solid {PALETTE['border']};
        border-left: 4px solid {PALETTE['blueprint']};
        border-radius: 4px;
        padding: 18px 22px;
        margin-bottom: 18px;
    }}
    .citation-box {{
        background-color: {PALETTE['paper']};
        border-left: 3px solid {PALETTE['blueprint_light']};
        padding: 10px 14px;
        margin: 4px 0 12px 220px;
        font-size: 0.85rem;
        color: {PALETTE['ink']};
    }}
    .citation-url {{
        font-family: 'IBM Plex Mono', monospace;
        font-size: 0.78rem;
        color: {PALETTE['blueprint']};
        word-break: break-all;
    }}
</style>
""", unsafe_allow_html=True)

CONFIDENCE_COLORS = {"high": PALETTE["high"], "medium": PALETTE["medium"], "low": PALETTE["low"], "none": PALETTE["none"]}


def confidence_badge(confidence: str) -> str:
    color = CONFIDENCE_COLORS.get(confidence, PALETTE["low"])
    return f'<span class="conf-badge" style="background-color:{color}">{confidence}</span>'


def render_product_card(mpn: str, p: dict):
    """
    Shared rendering for a single product - used by BOTH the pre-computed
    Product Detail tab and the live Upload & Enrich tab, so a result looks
    identical regardless of which path produced it. `p` is a plain dict
    matching ProductExtraction.model_dump()'s shape either way.
    """
    st.markdown(f"""
    <div class="header-card">
        <div class="mono" style="font-size:1.4rem; font-weight:600;">{mpn}</div>
        <div style="margin-top:4px; color:{PALETTE['blueprint']};">
            {p.get('brand') or '(brand not found)'} &nbsp;\u2022&nbsp; {p.get('manufacturer_name') or '(manufacturer not found)'}
        </div>
        <div class="mono" style="font-size:0.8rem; margin-top:8px; color:{PALETTE['blueprint_light']};">
            {p.get('mfr_url') or 'no manufacturer URL found'}
        </div>
    </div>
    """, unsafe_allow_html=True)

    attrs = p.get("attributes", [])
    filled = sum(1 for a in attrs if a["value"] is not None)
    high_conf = sum(1 for a in attrs if a["confidence"] == "high")
    c1, c2, c3 = st.columns(3)
    c1.metric("Fields filled", f"{filled}/{len(attrs)}" if attrs else "0/0")
    c2.metric("High confidence", f"{high_conf}/{len(attrs)}" if attrs else "0/0")
    c3.metric("Certifications", len(p.get("certifications", [])))

    if p.get("classpath"):
        st.caption(f"Classpath: {p['classpath']}")

    if not attrs:
        st.info("No attributes proposed - source content was too thin or irrelevant to extract from confidently.")
        return

    st.markdown("#### Attributes")
    for attr in attrs:
        value_display = attr["value"] if attr["value"] is not None else "\u2014"
        uom = f" {attr['uom']}" if attr.get("uom") else ""
        st.markdown(f"""
        <div class="spec-row">
            <div class="spec-label">{attr['label']}</div>
            <div class="spec-value">{value_display}{uom}</div>
            {confidence_badge(attr['confidence'])}
        </div>
        """, unsafe_allow_html=True)

        if attr.get("source_url"):
            with st.expander(f"View citation for {attr['label']}", expanded=False):
                st.markdown(f'<div class="citation-url">{attr["source_url"]}</div>', unsafe_allow_html=True)
                if attr.get("source_snippet"):
                    st.markdown(f'"{attr["source_snippet"]}"')


# ---------------------------------------------------------------------------
# Data loading - pre-computed batch, from what batch_run.py already produced
# ---------------------------------------------------------------------------
@st.cache_data
def load_batch():
    output_dir = Path("batch_output")
    if not output_dir.exists():
        return None, None, None

    products = {}
    for jf in output_dir.glob("*.json"):
        if jf.name == "cost_report.json":
            continue
        with open(jf) as f:
            products[jf.stem] = json.load(f)

    csv_rows = []
    csv_path = Path("batch_output.csv")
    if csv_path.exists():
        with open(csv_path, newline="", encoding="utf-8") as f:
            csv_rows = list(csv.DictReader(f))

    cost_report = None
    cost_path = output_dir / "cost_report.json"
    if cost_path.exists():
        with open(cost_path) as f:
            cost_report = json.load(f)

    return products, csv_rows, cost_report


products, csv_rows, cost_report = load_batch()

st.sidebar.markdown("## Unilog Enrichment Audit")
if products:
    st.sidebar.caption(f"{len(products)} products in pre-computed batch")

tab_product, tab_overview, tab_upload = st.tabs(["Product Detail", "Batch Overview", "Upload & Enrich"])

# ---------------------------------------------------------------------------
# Product Detail tab - pre-computed
# ---------------------------------------------------------------------------
with tab_product:
    if not products:
        st.markdown("### No batch results found yet")
        st.write("This tab reads `batch_output/` and `batch_output.csv` - run the pipeline first, or use **Upload & Enrich** to process your own data live.")
        st.code("python batch_run.py", language="powershell")
    else:
        # Column renamed to INTERNAL_Category when batch_run.py switched to
        # emitting Unilog's real 252-column schema - "Category" isn't a
        # real schema column, so it moved to our own namespaced QA columns.
        categories = sorted({row["INTERNAL_Category"] for row in csv_rows if row.get("Mfg_Part_Num") in products}) if csv_rows else []
        selected_category = st.sidebar.selectbox("Category", ["All"] + categories) if categories else "All"

        available_mpns = sorted(products.keys())
        if selected_category != "All" and csv_rows:
            available_mpns = [r["Mfg_Part_Num"] for r in csv_rows
                               if r["INTERNAL_Category"] == selected_category and r["Mfg_Part_Num"] in products]

        selected_mpn = st.sidebar.radio("Product", available_mpns) if available_mpns else None
        if selected_mpn:
            render_product_card(selected_mpn, products[selected_mpn])
        else:
            st.info("Select a product from the sidebar.")

# ---------------------------------------------------------------------------
# Batch Overview tab - the scalability/quality pitch panel
# ---------------------------------------------------------------------------
with tab_overview:
    if not products:
        st.info("No pre-computed batch to summarize yet - run `python batch_run.py` first.")
    else:
        st.markdown("### Batch-level quality")

        by_category = {}
        for row in csv_rows:
            if row["Mfg_Part_Num"] not in products:
                continue
            cat = row["INTERNAL_Category"]
            p = products[row["Mfg_Part_Num"]]
            if p["attributes"]:
                by_category.setdefault(cat, []).append(
                    sum(1 for a in p["attributes"] if a["value"] is not None) / len(p["attributes"])
                )

        cols = st.columns(len(by_category)) if by_category else []
        for col, (cat, rates) in zip(cols, by_category.items()):
            col.metric(cat, f"{sum(rates)/len(rates):.0%} avg fill", f"{len(rates)} products")

        st.markdown("### Cost & scalability")
        if cost_report:
            c1, c2, c3 = st.columns(3)
            c1.metric("Cost per SKU", f"${cost_report['cost_per_sku']:.5f}")
            c2.metric("Projected at 150K/mo", f"${cost_report['projected_current_volume']:,.0f}")
            c3.metric("Projected at 750K/mo", f"${cost_report['projected_target_volume']:,.0f}")
            st.caption(
                f"Measured from {cost_report['num_skus']} real SKUs this run - "
                f"{cost_report['gemini_calls']} Gemini calls, {cost_report['tavily_calls']} Tavily calls. "
                f"Tavily accounts for ${cost_report['tavily_cost']:.4f} of ${cost_report['total_cost']:.4f} total - "
                f"the search/retrieval layer dominates cost here, not the LLM call."
            )
        else:
            st.caption("No cost report found - run batch_run.py to generate one.")

# ---------------------------------------------------------------------------
# Upload & Enrich tab - the actually-dynamic path
# ---------------------------------------------------------------------------
with tab_upload:
    st.markdown("### Upload your own dataset")
    st.caption(
        "This calls the real pipeline live - actual Tavily search and Gemini extraction "
        "calls, not pre-computed data. Needs Mfg_Part_Num and Part_Desc columns at minimum; "
        "E1_Brand is used as a brand hint if present and not a placeholder value."
    )

    missing_keys = [k for k in ("TAVILY_API_KEY", "GEMINI_API_KEY") if not os.environ.get(k)]
    if missing_keys:
        st.error(
            f"Missing environment variable(s): {', '.join(missing_keys)}. "
            "Running locally: add them to your .env file. "
            "Running on Streamlit Community Cloud: add them under App settings -> Secrets instead - "
            "there is no .env file there."
        )
    else:
        uploaded_file = st.file_uploader("Upload a CSV", type="csv")

        if uploaded_file:
            upload_rows = list(csv.DictReader(io.StringIO(uploaded_file.getvalue().decode("utf-8"))))
            st.write(f"{len(upload_rows)} rows found. Preview:")
            st.dataframe(upload_rows[:10])

            missing_cols = [c for c in ("Mfg_Part_Num", "Part_Desc") if not upload_rows or c not in upload_rows[0]]
            if missing_cols:
                st.error(f"Missing required column(s): {missing_cols}")
            elif upload_rows:
                max_available = min(20, len(upload_rows))  # capped for live-demo safety, not a pipeline limit
                num_to_process = st.number_input(
                    "How many rows to process live (capped at 20 here for demo safety - the pipeline itself has no such limit)",
                    min_value=1, max_value=max_available, value=min(3, max_available)
                )

                if st.button("Run Enrichment", type="primary"):
                    from batch_run import process_row, build_csv_rows
                    from style_rules import clean_brand

                    domain_cache = st.session_state.get("upload_domain_cache", {})
                    results = []
                    progress = st.progress(0.0, text="Starting...")

                    for i, raw_row in enumerate(upload_rows[:num_to_process]):
                        mpn = raw_row["Mfg_Part_Num"]
                        desc = raw_row["Part_Desc"]
                        # Brand fallback chain, in order - verified against the real
                        # 1000-row hackathon sample, not assumed:
                        #   E1_Brand:     populated on ~20% of real rows
                        #   Unilog_Brand: populated on 0% of real rows (always the
                        #                 placeholder) - checked anyway since it's free,
                        #                 but never actually contributes on real data
                        #   DIB_Brand:    populated on an ADDITIONAL ~24% of rows beyond
                        #                 E1_Brand, and confirmed to NEVER conflict with
                        #                 E1_Brand when both are checked across the full
                        #                 sample (0 disagreements in 1000 rows) - safe to
                        #                 add as a fallback, no tie-break logic needed.
                        # Adding DIB_Brand roughly DOUBLES real brand-hint coverage
                        # (20% -> 44%), which matters a lot: every row without a brand
                        # hint falls back to identify_brand_from_search(), the weaker,
                        # confirmed-nondeterministic resolution path (see README). More
                        # rows routed through the reliable path is a real accuracy win,
                        # not a cosmetic one.
                        #
                        # Part_Manuf is deliberately NOT included here despite being
                        # populated on 100% of real rows - verified it's Unilog's
                        # internal supplier/distributor code field, not a manufacturer
                        # field (e.g. a genuine 3M product listed under Part_Manuf
                        # "Jam Industrial Supply LLC (JAMIN)" - a distributor, not 3M).
                        # Using it as a brand hint would risk resolving straight to a
                        # distributor's domain, which is exactly the hard-rule
                        # violation this whole pipeline is built to prevent.
                        brand = (clean_brand(raw_row.get("E1_Brand", ""))
                                 or clean_brand(raw_row.get("Unilog_Brand", ""))
                                 or clean_brand(raw_row.get("DIB_Brand", "")))
                        progress.progress(i / num_to_process, text=f"Processing {mpn}...")
                        row_for_pipeline = {"mfg_part_num": mpn, "part_desc": desc, "brand": brand}
                        result = process_row(row_for_pipeline, domain_cache)
                        results.append(result)
                        progress.progress((i + 1) / num_to_process, text=f"Done: {mpn}")

                    st.session_state["upload_results"] = results
                    st.session_state["upload_domain_cache"] = domain_cache
                    st.success(f"Processed {len(results)} row(s) live.")

        if "upload_results" in st.session_state:
            results = st.session_state["upload_results"]
            succeeded = [r for r in results if r["extraction"] is not None]
            failed = [r for r in results if r["extraction"] is None]

            st.markdown("---")
            st.markdown(f"### Results ({len(succeeded)} succeeded, {len(failed)} did not)")

            if succeeded:
                mpns = [r["row"]["mfg_part_num"] for r in succeeded]
                selected = st.radio("View result", mpns, horizontal=True)
                selected_result = next(r for r in succeeded if r["row"]["mfg_part_num"] == selected)
                render_product_card(selected, selected_result["extraction"].model_dump())

                from batch_run import build_csv_rows
                out_rows, fieldnames = build_csv_rows(results)
                buf = io.StringIO()
                writer = csv.DictWriter(buf, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(out_rows)
                st.download_button("Download these results as CSV", buf.getvalue(), "live_enrichment_results.csv", "text/csv")

            if failed:
                with st.expander(f"{len(failed)} row(s) with no result"):
                    for r in failed:
                        reason = r["error"] or r["note"] or "unknown"
                        st.write(f"**{r['row']['mfg_part_num']}**: {reason}")
