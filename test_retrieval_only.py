"""
test_retrieval_only.py

Quick diagnostic - just retrieval, no extraction. Testing whether the
"owner-support-portal outranks marketing/spec page" problem we saw on two
appliance brands also happens on a more B2B/industrial category, or if
that's specific to big consumer-appliance retail sites.

Usage:
    python test_retrieval_only.py "49-94-0013" "Milw 5in x.045in x7/8in Metal Cut Off Disc"
"""

import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / ".env", encoding="utf-8-sig")

from retrieval import search_product_verified

mfg_part_num, part_desc = sys.argv[1], sys.argv[2]
brand = sys.argv[3] if len(sys.argv) > 3 else None
query = f"{mfg_part_num} specifications"
print(f"Searching: {query!r} (brand={brand!r})")

sources = search_product_verified(query, mfg_part_num, brand=brand, max_results=5)

print(f"\n{len(sources)} source(s) survived:")
for i, s in enumerate(sources, 1):
    print(f"  {s['url']}")
    print(f"    content length: {len(s['content'])} chars")
    outfile = f"debug_source_{i}.txt"
    with open(outfile, "w", encoding="utf-8") as f:
        f.write(s["content"])
    print(f"    full content written to {outfile}")
    print()
