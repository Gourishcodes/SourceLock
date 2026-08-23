"""
retrieval.py

Two-layer enforcement of "manufacturer sources only, never marketplaces":
  Layer 1: Tavily's exclude_domains param - filters before content is ever fetched
  Layer 2: our own re-check on every returned URL - trust nothing, verify again

IMPORTANT - lesson from the first real test run: Layer 1/2 above only catch
domains we already know about (Amazon, Grainger, McMaster...). They do NOT
catch the long tail of regional appliance retailers, furniture stores, and
parts distributors - a real search for KDFM404KPS returned
alabamaapplianceoutlet.com, us-appliance.com, colders.com (a review blog),
and wise-furnitureco.com alongside the real kitchenaid.com result. No static
denylist can enumerate that long tail.

So the real guarantee comes from search_product_verified() below: it scores
candidate domains, picks the single best manufacturer-looking one, then
re-searches LOCKED to that domain only (include_domains). Content from any
other domain is never passed to the LLM at all - not "deprioritized", never
fetched for extraction in the first place. If no domain scores confidently
enough, it returns nothing rather than falling back to unverified sources -
"not found" is a correct answer, silently citing a retailer is not.
"""

import os
import re
import difflib
from typing import Optional
from urllib.parse import urlparse, unquote
from tavily import TavilyClient
import usage_tracker


# Marketplace / non-manufacturer distributor domains we know by name.
# Kept as a cheap first-pass filter, but NOT relied on as the real
# guarantee - see search_product_verified().
MARKETPLACE_DENYLIST = {
    "amazon.com", "ebay.com", "walmart.com", "target.com",
    "homedepot.com", "lowes.com", "grainger.com", "zoro.com",
    "mcmaster.com", "wayfair.com", "etsy.com", "alibaba.com",
    "aliexpress.com", "newegg.com", "bestbuy.com", "overstock.com",
    "houzz.com", "build.com", "ferguson.com", "supplyhouse.com",
}

# Words that show up disproportionately in retailer/distributor domains
# and URL paths. Not exhaustive by design - this is a scoring signal,
# not a guarantee. The guarantee is include_domains locking in
# search_product_verified().
RETAILER_DOMAIN_WORDS = {
    "outlet", "furniture", "shop", "deal", "deals", "store", "sale",
    "sales", "supply", "warehouse", "discount", "buy", "appliancepart",
}
RETAILER_PATH_WORDS = {
    "cart", "checkout", "review", "reviews", "blog", "shop", "buy",
}
MANUFACTURER_PATH_WORDS = {
    "spec", "specs", "specification", "documentation", "manual",
}
# Removed "owners-center"/"owner-center"/"support"/"product-support"/"pdp" -
# real evidence from two unrelated manufacturers (KitchenAid, GE Appliances)
# showed these paths are consistently JS-gated shells with almost no static
# content, despite sounding like exactly the right page. Sounding right and
# containing content are different things - don't reward the former.

# NEW, evidenced by both tests: these are owner-SUPPORT tool pages (order a
# part, contact support, troubleshoot a fault), not spec/marketing pages.
# They rank highly for "{mpn} ... specifications" queries specifically
# BECAUSE they're indexed by exact model number - that's their purpose, not
# a sign of relevance. Playwright rendering confirmed these are genuinely
# thin, not just JS-delayed - actively penalize, don't just fail to reward.
RETAILER_PATH_WORDS = RETAILER_PATH_WORDS | {
    "parts", "troubleshoot", "owners-center", "owner-center", "owner", "regist", "support",
}


def _domain_of(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def looks_like_owner_support_page(url: str) -> bool:
    """
    Third confirmed instance of the same pattern, on a third brand:
    KitchenAid's owners-center, GE's /support//troubleshoot/parts, and now
    Frigidaire's owner.frigidaire.com - all generic troubleshooting/support
    hubs that rank highly for "{mpn} specifications" queries (they're
    indexed by model number) despite having zero real spec content.

    RETAILER_PATH_WORDS only ever checked the URL PATH - it missed
    owner.frigidaire.com because "owner" was in the SUBDOMAIN, not the
    path. Checking the full netloc+path together catches both shapes.
    """
    full = urlparse(url).netloc.lower() + urlparse(url).path.lower()
    return any(w in full for w in RETAILER_PATH_WORDS)


def is_denylisted(url: str) -> bool:
    domain = _domain_of(url)
    return any(domain == d or domain.endswith("." + d) for d in MARKETPLACE_DENYLIST)


def _score_candidate(url: str, title: str) -> float:
    """
    Higher = more likely to be the manufacturer's own domain.
    Heuristic, not certain - real guarantee is the include_domains lock
    that happens after a domain is picked, not this score by itself.
    """
    domain = _domain_of(url)
    second_level = domain.split(".")[0]
    path = urlparse(url).path.lower()
    score = 0.0

    # Real manufacturer domains tend to be a single short brand word:
    # kitchenaid.com, whirlpool.com, geappliances.com. Distributor/retailer
    # names tend to be longer, hyphenated, and descriptive.
    if "-" in second_level:
        score -= 2.0
    if len(second_level) > 14:
        score -= 1.5
    if len(second_level) <= 10 and "-" not in second_level:
        score += 1.0

    if any(w in domain for w in RETAILER_DOMAIN_WORDS):
        score -= 3.0
    if any(w in path for w in RETAILER_PATH_WORDS):
        score -= 2.0
    if any(w in path for w in MANUFACTURER_PATH_WORDS):
        score += 2.5

    if is_denylisted(url):
        score -= 10.0

    return score


def pick_best_manufacturer_domain(results: list[dict], min_score: float = 1.0) -> Optional[str]:
    """
    Given raw search results, score each candidate domain and return the
    single best one - only if it clears min_score. Below that bar we'd
    rather report 'no confident source' than guess.
    """
    if not results:
        return None
    scored = [(_score_candidate(r["url"], r.get("title", "")), _domain_of(r["url"])) for r in results]
    scored.sort(reverse=True, key=lambda x: x[0])
    best_score, best_domain = scored[0]
    return best_domain if best_score >= min_score else None


def _raw_search(query: str, include_domains: Optional[list[str]] = None, max_results: int = 5) -> list[dict]:
    client = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
    kwargs = dict(
        query=query,
        search_depth="advanced",  # advanced = 2 credits/call, not 1 - matters for cost.py
        max_results=max_results,
        exclude_domains=list(MARKETPLACE_DENYLIST),
    )
    if include_domains:
        kwargs["include_domains"] = include_domains
    raw = client.search(**kwargs)
    usage_tracker.record_tavily_call()
    return raw.get("results", [])


def _normalize_for_match(s: str) -> str:
    """Lowercase, strip everything but letters/digits - for comparing a
    brand name to a domain's second-level name on equal footing."""
    return re.sub(r'[^a-z0-9]', '', s.lower())


# Two-part public-suffix TLDs where the registrable brand name sits one
# level further back than usual. CONFIRMED real failure, not speculative:
# "crompton.co.in" -> parts[-2] is "co", not "crompton" - this made a
# genuine Indian manufacturer (Crompton) score 0.35 similarity against its
# own real domain and get rejected below the 0.6 confidence threshold,
# even though the domain resolution was otherwise working correctly.
# Small hardcoded set rather than a full public-suffix-list dependency -
# covers the Indian/Commonwealth-style domains this catalog is actually
# likely to hit, not every possible ccTLD combination in the world.
MULTI_PART_TLDS = {
    "co.in", "co.uk", "co.za", "co.nz", "co.jp", "co.kr",
    "com.au", "com.br", "com.sg", "com.my",
}


def _registrable_segment(domain: str) -> str:
    """
    Real bug this fixes: comparing against domain.split('.')[0] treats
    "en.wikipedia.org" as "en" - a language subdomain, not the actual
    brand-relevant name. "en" happens to be a literal substring of
    "kitchenaid" ("kitchEN aid"), which triggered a false 0.80-similarity
    match and resolved KitchenAid's domain to Wikipedia.

    Fix: if there's a subdomain prefix (3+ dot-separated parts), use the
    SECOND-TO-LAST part (the actual registrable name, right before the
    TLD) instead of blindly taking the first part. "en.wikipedia.org" ->
    "wikipedia" (correct). "products.geappliances.com" -> "geappliances"
    (also correct, and arguably better than what we compared before).

    SECOND fix, added after a real failure on an Indian brand: multi-part
    TLDs like ".co.in" break the "second-to-last part" assumption above,
    because the brand name sits at parts[-3], not parts[-2].
    "crompton.co.in" -> without this check, "co" (wrong). With it,
    "crompton" (correct). See MULTI_PART_TLDS above for the covered set.
    """
    parts = domain.split(".")
    last_two = ".".join(parts[-2:])
    if len(parts) > 2 and last_two in MULTI_PART_TLDS:
        return parts[-3] if len(parts) > 2 else parts[0]
    return parts[-2] if len(parts) > 2 else parts[0]


# Manual overrides for brands where search-based resolution has been
# checked and found unreliable, but the real domain is well-established
# and easy to verify by hand. Real example that justified this: "Frigidaire"
# scored only 0.18 via search (frigidaire.com's own search presence is
# crowded out by retailer/parts listings), despite frigidaire.com being
# Frigidaire's confirmed, unambiguous official site. Add entries here as
# you spot-check your batch's brand list - this is meant to stay small.
KNOWN_BRAND_DOMAINS = {
    "frigidaire": "frigidaire.com",
    # The following six are added after repeated real evidence tonight that
    # automatic resolution is unreliable for them specifically - not
    # speculative, each has a documented failure:
    #   Milwaukee: resolved to milwaukeetool.eu on two separate runs (the
    #     .com candidate simply wasn't returned by search that day - a TLD
    #     tiebreaker can't fix a tie that doesn't happen)
    #   Kichler: resolved to kichlerlightinglights.com - an adversarial
    #     domain that embeds "kichler" as a prefix inside a much longer,
    #     generic-word-padded name. Tried a coverage-based penalty for this
    #     class of case; it also rejects geappliancesco.com (legitimate),
    #     so a scoring fix isn't clean here - a verified override is safer
    #     than chasing an approach with a demonstrated ceiling.
    #   KitchenAid: resolved to en.wikipedia.org due to the subdomain bug
    #     (fixed separately) - kept as a verified entry anyway since it's
    #     now double-checked and removes any remaining re-resolution risk.
    # GE/LG/Whirlpool: consistently correct across many runs, added for the
    #     same reason - once verified, no reason to keep re-rolling the dice.
    "milwaukee": "milwaukeetool.com",
    "kichler": "kichler.com",
    "kitchenaid": "kitchenaid.com",
    "ge": "geappliances.com",
    "lg": "lg.com",
    "whirlpool": "whirlpool.com",
    # Crompton: real failure tonight testing Indian-market SKUs - resolved
    # to only 0.35 similarity and got rejected below the 0.6 threshold.
    # Root cause was the .co.in multi-part-TLD bug (see MULTI_PART_TLDS /
    # _registrable_segment above), which is now fixed generically - but
    # kept as a verified override too, same reasoning as KitchenAid above:
    # once hand-checked, no reason to keep re-rolling the dice on a brand
    # that's central to this catalog's actual target market.
    "crompton": "crompton.co.in",
}


def resolve_manufacturer_domain(brand: str, qualifier: Optional[str] = None, min_similarity: float = 0.6) -> Optional[str]:
    """
    Dedicated brand -> official domain resolution, run ONCE PER BRAND (cache
    the result yourself across a batch - this should never run per-SKU).

    qualifier: optional product category/type appended to the search query
    (e.g. "dishwasher"). Matters for brands whose bare name resolves to the
    wrong entity - real example: "GE" alone resolves to ge.com, General
    Electric's industrial conglomerate site (aerospace, healthcare, power).
    GE Appliances has been owned by Haier since 2016 and lives on a
    different domain entirely. "GE dishwasher official website" is a
    meaningfully different, more specific query that's more likely to
    surface the actual appliances site instead.

    Why this is a different, more reliable question than the product-search
    scoring in pick_best_manufacturer_domain(): a product query ("{mpn}
    specs") surfaces whatever page best matches THAT query, which is very
    often a well-built retailer page - that's how we ended up on
    pcstools.com for a Milwaukee product, a real hard-rule violation. A
    dedicated "{brand} official website" search is a different, better-
    behaved query pattern, AND here we can fuzzy-match the returned domain
    against the brand name itself, which the generic scorer had no way to
    do (it had no brand to compare against).

    CAVEAT, stated plainly: this is still not a guarantee. The same search
    that found milwaukeetool.com also returned themilwaukeetools.com - a
    domain deliberately named to resemble the real one, which is actually
    an Amazon affiliate site. String similarity alone cannot catch a
    deliberately deceptive domain name. For your actual pilot batch (a
    finite, small list of maybe 10-20 unique brands), manually eyeball the
    resolved brand->domain map before demo day - that's a cheap, checkable
    safety net that doesn't require solving "detect fake domains" in
    general, which no heuristic here claims to do.
    """
    override = KNOWN_BRAND_DOMAINS.get(_normalize_for_match(brand))
    if override:
        print(f"[retrieval] Using manually-verified domain for '{brand}': {override}")
        return override

    # Try the qualified query first (it's what correctly resolved GE to
    # geappliancesco.com instead of the wrong ge.com), but fall back to the
    # plain brand-only query if that doesn't confidently resolve - real
    # regression this caught: "Milwaukee metal cut-off disc official
    # website" is an unnaturally narrow compound query that failed to find
    # milwaukeetool.com at all, when the plain "Milwaukee official website"
    # had found it reliably in every prior test. A qualifier that helps
    # disambiguate one brand shouldn't be allowed to silently break another
    # that never needed disambiguating in the first place.
    domain, score = _try_resolve(brand, qualifier) if qualifier else (None, 0.0)
    if domain and score >= min_similarity:
        print(f"[retrieval] Resolved '{brand}' -> {domain} (similarity {score:.2f}, qualified query)")
        return domain

    domain, score = _try_resolve(brand, None)
    if domain and score >= min_similarity:
        print(f"[retrieval] Resolved '{brand}' -> {domain} (similarity {score:.2f}, plain query{' - qualified attempt failed first' if qualifier else ''})")
        return domain

    print(f"[retrieval] Could not confidently resolve official domain for '{brand}' "
          f"(best candidate scored {score:.2f}, below {min_similarity})")
    return None


def _try_resolve(brand: str, qualifier: Optional[str]) -> tuple[Optional[str], float]:
    query = f"{brand} {qualifier} official website" if qualifier else f"{brand} official website"
    results = _raw_search(query, max_results=5)
    brand_norm = _normalize_for_match(brand)

    best_domain = None
    best_score = 0.0
    for r in results:
        if is_denylisted(r["url"]):
            continue
        domain = _domain_of(r["url"])
        second_level = _normalize_for_match(_registrable_segment(domain))
        ratio = difflib.SequenceMatcher(None, brand_norm, second_level).ratio()
        if brand_norm in second_level or second_level in brand_norm:
            ratio = max(ratio, 0.8)

        # TLD preference - real regression this caught: "Milwaukee" resolved
        # to milwaukeetool.eu on one run and milwaukeetool.com on another,
        # purely due to search-result ordering variance, because comparing
        # only the second-level domain name treats them as IDENTICAL - .eu
        # and .com both normalize to "milwaukeetool". For a US distributor
        # catalog, .com should win ties, not be decided by which regional
        # site happened to rank first that day.
        #
        # REMOVED the blanket ccTLD penalty that used to sit here. It was
        # tuned for that one narrow US-regional tie-break, but as written it
        # penalized ANY two-letter-TLD domain - including legitimate
        # manufacturer domains like "crompton.co.in". On an India-focused
        # catalog that's not a rare edge case, it's a large fraction of the
        # real manufacturer domains we need to resolve correctly. A small
        # same-brand tie-break preference for .com is harmless and stays;
        # a blanket penalty against non-.com domains is not.
        tld = domain.split(".")[-1]
        if tld == "com":
            ratio += 0.05

        if ratio > best_score:
            best_score, best_domain = ratio, domain

    return best_domain, best_score


def identify_brand_from_search(mfg_part_num: str, product_type: str) -> Optional[str]:
    """
    Replaces the old shape-based pick_best_manufacturer_domain() as the
    fallback for SKUs with no brand hint in the input data. Real evidence
    from a batch run: the shape-based fallback landed on ftp.kerusso.com
    (an unrelated Christian-apparel company's forgotten subdomain hosting
    SEO spam), facebook.com, and two different retailer sites, across 5
    real test rows - only 1/5 was actually correct. Scoring arbitrary
    domains by generic shape has no real connection to "is this the
    manufacturer" and that approach is now retired for production use.

    This does a broad search, shows Gemini ONLY the result titles/URLs (not
    full page content - keeps this cheap, it's a small classification task
    not an extraction task), and asks it to name the likely brand if the
    titles make it reasonably obvious. Returns None if unclear - a missed
    brand identification is far cheaper than a wrong one, since None
    correctly leads to "no sources found" rather than a hard-rule violation.

    RETRY, added after a real observed failure: the qualified query
    "{mpn} {product_type} specifications" returned ZERO relevant results
    for a genuine Indian SKU (Crompton CFHSHS42OPW1S) - confirmed by
    checking what a general web search actually surfaces for that literal
    string: unrelated US ceiling-fan spec pages (ENERGY STAR, Home Depot),
    nothing mentioning Crompton at all. The word "specifications" tacked
    onto a thinly-indexed model number can actively hurt recall rather
    than help it. Same exact input previously succeeded on a different
    run (live search index isn't static), so this was causing real
    non-determinism - same SKU, same code, different pass/fail depending
    on what Tavily's index happened to rank that moment. A bare
    model-number-only query is a strictly broader net and costs one extra
    Tavily call ONLY when the first attempt already came back empty -
    never fires on the (much more common) case where the qualified query
    already found something.
    """
    from google import genai
    from pydantic import BaseModel

    results = _raw_search(f"{mfg_part_num} {product_type}", max_results=5)
    if not results:
        print(f"[retrieval] No results for qualified query - retrying with bare model number")
        results = _raw_search(mfg_part_num, max_results=5)
    if not results:
        return None

    titles_block = "\n".join(f"- {r.get('title', '')} ({_domain_of(r['url'])})" for r in results)

    class BrandGuess(BaseModel):
        brand: Optional[str] = None

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    prompt = f"""Search result titles for the query "{mfg_part_num} {product_type}":
{titles_block}

Based ONLY on these titles, what brand/manufacturer makes this product?
If it's not reasonably clear from the titles, return null - do not guess."""

    resp = client.models.generate_content(
        model="gemini-flash-lite-latest",  # alias, not a pinned version - see extraction.py comment
        contents=prompt,
        config={"response_mime_type": "application/json", "response_schema": BrandGuess},
    )
    usage_tracker.record_gemini_response(resp, prompt)
    guess = BrandGuess.model_validate_json(resp.text)
    if guess.brand:
        print(f"[retrieval] Identified likely brand from search titles: '{guess.brand}'")
    else:
        # Previously a silent dead end - a None result gave no way to tell
        # whether the titles were genuinely brand-less or Gemini was being
        # overly conservative on titles that DID contain the brand.
        # Confirmed real case: a Crompton SKU failed here with results
        # present (retry above never even fired), no visibility into why.
        # Printing the titles turns the next failure into something
        # debuggable instead of another blind guess.
        print(f"[retrieval] Could not identify a brand from search titles. Titles seen:\n{titles_block}")
    return guess.brand


def _looks_thin(content: str) -> bool:
    """
    Heuristic for 'this static fetch probably missed the real spec table'.
    Our domain is industrial/hardware products - real spec content is
    dense with number+unit patterns (120V, 15A, 24 in, 44 dBA...). If a
    page has almost none of those, it's very likely a JS-rendered shell
    we're seeing pre-render, not a page that's genuinely spec-free.
    """
    pattern = r'\b\d[\d./\-]*\s?(v|a|amp|amps|in|inch|inches|ft|db|dba|lb|lbs|kg|w|watt|watts|hz|cu\.?\s?ft)\b'
    matches = re.findall(pattern, content, re.IGNORECASE)
    return len(matches) < 3


def render_with_playwright(url: str, timeout_ms: int = 15000) -> Optional[str]:
    """
    Fallback for JS-heavy pages where the static fetch returned mostly
    boilerplate. Launches headless Chromium, returns the RENDERED page's
    visible text - i.e. what a real browser would show after JS runs, not
    the pre-render HTML shell.

    Deliberately only called when _looks_thin() flags a page - this is
    slow (multiple seconds per call) and would wreck the "one cheap call
    per product" cost story if it ran on every request instead of as an
    escalation for the specific pages that need it.

    Three changes here target real, distinct failure patterns observed
    across tonight's testing, not speculative tuning:

    1. --disable-http2: KitchenAid, Whirlpool, and Frigidaire consistently
       fail with net::ERR_HTTP2_PROTOCOL_ERROR - every single attempt,
       across every run tonight - while GE/LG/Milwaukee/Kichler show
       timeouts instead, a different failure mode. A server resetting the
       HTTP/2 connection mid-handshake is a classic bot-detection
       signature (Akamai/Cloudflare Bot Management fingerprinting
       Chromium's default HTTP/2 SETTINGS frame). Forcing HTTP/1.1
       sidesteps whatever's fingerprinting the HTTP/2 handshake
       specifically. UNVERIFIED without a live test - this is the most
       direct available lever for the exact error observed, not a
       guarantee it resolves the underlying bot detection entirely.

    2. --disable-blink-features=AutomationControlled + a real Chrome user
       agent: reduces the more basic automation fingerprints (the default
       navigator.webdriver flag, a giveaway "HeadlessChrome" UA string)
       that bot detection commonly checks first.

    3. wait_until="load" + a short explicit wait, instead of "networkidle":
       networkidle is very plausibly WHY GE/LG/Milwaukee pages are timing
       out - a page with any persistent background connection (analytics
       beacon, chat widget, polling) never truly goes network-idle and
       just hangs until timeout. "load" fires once, then a short explicit
       wait gives async JS content a chance to populate without waiting
       for network activity to fully settle.

    Requires: pip install playwright && playwright install chromium
    Requires network access - run on your own machine, not this sandbox.
    """
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-http2",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
            )
            page = context.new_page()
            page.goto(url, timeout=timeout_ms, wait_until="load")
            page.wait_for_timeout(2000)  # let async JS content populate after the load event
            text = page.inner_text("body")
            context.close()
            browser.close()
            return text
    except Exception as e:
        print(f"[retrieval] Playwright render failed for {url}: {e}")
        return None


def search_product_verified(query: str, mfg_part_num: str, product_type: str = "", brand: Optional[str] = None,
                             max_results: int = 5, domain_cache: Optional[dict] = None) -> list[dict]:
    """
    Retrieval, in order of preference:

    1. If `brand` is known (from E1_Brand when not a placeholder, or parsed
       from Part_Desc): resolve_manufacturer_domain(brand, qualifier=product_type).
       This is the reliable path - proven to catch the pcstools.com/Milwaukee
       failure, and the qualifier catches cases like "GE" resolving to
       ge.com (General Electric's industrial conglomerate site) instead of
       the actual appliances business, which has been owned by Haier since
       2016 and lives on a different domain.

    2. If brand is unknown: identify_brand_from_search() first, THEN route
       through the same reliable resolve_manufacturer_domain() path above.
       RETIRED: the old shape-based pick_best_manufacturer_domain() fallback
       is no longer used here - real batch evidence showed it landing on
       ftp.kerusso.com (an unrelated company's spam-hosting subdomain) and
       facebook.com, 4 hard-rule violations out of 5 real test rows. If
       brand identification also fails, this returns [] - a missed
       identification correctly means "not found", not a guess.

    domain_cache: pass a dict (e.g. {} created once before your batch loop)
    to cache brand->domain resolution across a whole batch run.

    Either way: once a domain is chosen, re-search LOCKED to it
    (include_domains) - that's the actual hard-rule guarantee, not the
    domain-picking step itself.

    Returns [] if no domain can be resolved confidently, or if the locked
    re-search comes back empty. Both are valid "not found" outcomes.

    Requires network access to api.tavily.com - run this on your own machine.
    """
    domain = None
    if not brand:
        # No caching here on purpose - each unbranded MPN is a different
        # product, there's nothing to reuse across rows for this step.
        # Only the brand->domain mapping below benefits from caching,
        # since multiple SKUs (given or identified) can share a brand.
        brand = identify_brand_from_search(mfg_part_num, product_type)

    if brand:
        if domain_cache is not None and brand in domain_cache:
            domain = domain_cache[brand]
            print(f"[retrieval] Using cached domain for '{brand}': {domain}")
        else:
            domain = resolve_manufacturer_domain(brand, qualifier=product_type)
            if domain_cache is not None:
                domain_cache[brand] = domain  # cache even None - don't re-attempt a brand that failed to resolve

    if domain is None:
        print(f"[retrieval] No confidently-manufacturer domain resolved for {mfg_part_num} - reporting not found")
        return []
    print(f"[retrieval] Locking to verified domain: {domain}")

    locked_results = _raw_search(query, include_domains=[domain], max_results=max_results)

    seen_paths = set()
    deduped = []
    for r in locked_results:
        key = unquote(urlparse(r["url"]).path).lower()
        if key in seen_paths:
            continue
        seen_paths.add(key)
        deduped.append(r)

    clean_results = [r for r in deduped if not is_denylisted(r["url"])]

    # Drop localized/international site variants - real example that
    # justified this: milwaukeetool.com/es/... returned a broken Spanish
    # page ("technical error processing your request") full of unrelated
    # product names, not real content for this SKU. Locale segments are a
    # reliable enough URL pattern across most manufacturer sites.
    LOCALE_SEGMENTS = {"es", "fr", "de", "it", "pt", "mx", "ca", "jp", "cn", "kr"}
    def _is_localized(url: str) -> bool:
        segments = urlparse(url).path.lower().split("/")
        return any(seg in LOCALE_SEGMENTS for seg in segments)
    before = len(clean_results)
    clean_results = [r for r in clean_results if not _is_localized(r["url"])]
    if len(clean_results) < before:
        print(f"[retrieval] Dropped {before - len(clean_results)} localized site variant(s)")

    mpn = mfg_part_num.lower()
    # Drop API/data-endpoint URLs, not real pages - real case that
    # justified this: crompton.co.in returned a ".oembed" URL (Shopify's
    # oEmbed API endpoint, not a rendered product page). It was fetched,
    # found thin, escalated to Playwright, which then tried to DOWNLOAD it
    # as a file rather than render HTML ("Page.goto: Download is
    # starting") - wasted fetch and wasted render for a URL that could
    # never contain spec content in the first place.
    NON_PAGE_SUFFIXES = (".oembed", ".json", ".xml", ".rss", ".atom")
    before = len(clean_results)
    clean_results = [r for r in clean_results
                      if not any(r["url"].lower().split("?")[0].endswith(sfx) for sfx in NON_PAGE_SUFFIXES)]
    if len(clean_results) < before:
        print(f"[retrieval] Dropped {before - len(clean_results)} non-page API/data endpoint(s)")

    exact_match = [r for r in clean_results
                   if mpn in r["url"].lower() or mpn in r.get("title", "").lower()]
    selected = exact_match if exact_match else clean_results
    if exact_match:
        print(f"[retrieval] {len(exact_match)}/{len(clean_results)} results mention {mfg_part_num} directly, using those")
    else:
        print(f"[retrieval] No result URL/title mentioned {mfg_part_num} exactly - using all {len(clean_results)} verified-domain results")

    # Sort real product/spec pages ahead of generic owner-support hubs -
    # doesn't invent a real spec page that isn't indexed (can't fix that),
    # but when both exist in the result set, makes sure the useful one
    # doesn't lose out to a support article that happens to rank higher.
    support_count = sum(1 for r in selected if looks_like_owner_support_page(r["url"]))
    if 0 < support_count < len(selected):
        selected = sorted(selected, key=lambda r: looks_like_owner_support_page(r["url"]))
        print(f"[retrieval] Deprioritized {support_count} owner-support-style page(s) in favor of product pages")

    # Dedupe near-identical CONTENT, not just identical paths - real bug
    # this fixes: seen_paths above only catches exact URL-path collisions,
    # but a confirmed real case (Havells FHCEO5SBNC48-C) returned the SAME
    # page reachable at two different paths (a canonical URL and a
    # category-nested one), each with byte-identical 2283-char static
    # content. Both were then independently sent through
    # render_with_playwright() - doubling JS-render time/cost for zero new
    # information. Comparing a normalized prefix is cheap (no network
    # call) and catches this before the expensive step runs, not after.
    def _content_fingerprint(content: str) -> str:
        return re.sub(r'\s+', ' ', content.strip().lower())[:500]

    deduped_by_content = []
    seen_fingerprints = set()
    dupes_dropped = 0
    for r in selected:
        fp = _content_fingerprint(r.get("content", ""))
        if fp and fp in seen_fingerprints:
            dupes_dropped += 1
            continue
        if fp:
            seen_fingerprints.add(fp)
        deduped_by_content.append(r)
    if dupes_dropped:
        print(f"[retrieval] Dropped {dupes_dropped} URL(s) with duplicate content "
              f"(same page reachable via multiple paths) - avoids redundant JS renders")
    selected = deduped_by_content

    final = []
    for r in selected:
        content = r.get("content", "")
        if _looks_thin(content):
            print(f"[retrieval] {r['url']} looks thin ({len(content)} chars, few spec-like patterns) - trying JS render")
            rendered = render_with_playwright(r["url"])
            if rendered and not _looks_thin(rendered):
                print(f"[retrieval]   recovered richer content via render ({len(rendered)} chars)")
                content = rendered
            else:
                print(f"[retrieval]   render didn't help, keeping static content as-is")
        final.append({"url": r["url"], "title": r.get("title", ""), "content": content})

    return final


def search_product(query: str, include_domains: Optional[list[str]] = None, max_results: int = 5) -> list[dict]:
    """Kept for reference/testing - unlocked search. Prefer search_product_verified()
    for anything that actually feeds extraction."""
    results = _raw_search(query, include_domains=include_domains, max_results=max_results)
    clean_results = []
    blocked = []
    for r in results:
        if is_denylisted(r["url"]):
            blocked.append(r["url"])
            continue
        clean_results.append({"url": r["url"], "title": r.get("title", ""), "content": r.get("content", "")})
    if blocked:
        print(f"[retrieval] Layer 2 caught {len(blocked)} url(s) Layer 1 missed: {blocked}")
    return clean_results

