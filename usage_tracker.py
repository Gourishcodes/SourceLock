"""
usage_tracker.py

Counters, incremented by retrieval.py and extraction.py as real API calls
happen. cost.py reads these to compute cost from ACTUAL measured usage
during a batch run, not from assumed averages - much more credible to show
a judge "here's what our real test batch cost" than "here's our estimate
of what it might cost."

THREAD-LOCAL, not a plain module dict - real bug this fixes: a plain
module-level dict is shared by every user hitting the deployed Streamlit
app, because Streamlit Cloud runs one Python process serving all sessions.
Two people clicking "Run Enrichment" around the same time would silently
mix each other's token/call counts into one shared total, corrupting the
cost-per-SKU number for both - exactly the kind of thing that's invisible
in solo local testing and only shows up live in front of a judge.
threading.local() gives each thread (Streamlit runs each session's script
execution in its own thread) its own isolated copy - same public API below,
every caller (retrieval.py, extraction.py, classification.py, cost.py,
batch_run.py's CLI path) needs zero changes.
"""

import threading

_local = threading.local()


def _usage() -> dict:
    if not hasattr(_local, "usage"):
        _local.usage = {
            "tavily_calls": 0,
            "gemini_input_tokens": 0,
            "gemini_output_tokens": 0,
            "gemini_calls": 0,
        }
    return _local.usage


def record_tavily_call():
    _usage()["tavily_calls"] += 1


def record_gemini_call(input_tokens: int, output_tokens: int):
    u = _usage()
    u["gemini_calls"] += 1
    u["gemini_input_tokens"] += input_tokens
    u["gemini_output_tokens"] += output_tokens


def record_gemini_response(resp, prompt: str):
    """
    Shared by extraction.py and retrieval.py (identify_brand_from_search
    also calls Gemini) - one place to parse token counts out of a genai
    response, so a wrong guess about the SDK's attribute shape only needs
    fixing once. Defensive on purpose: falls back to a ~4 chars/token
    estimate rather than crash a batch run over a cost-tracking nicety.
    """
    try:
        input_tokens = resp.usage_metadata.prompt_token_count
        output_tokens = resp.usage_metadata.candidates_token_count
    except AttributeError:
        input_tokens = len(prompt) // 4
        output_tokens = len(resp.text) // 4 if hasattr(resp, "text") else 0
    record_gemini_call(input_tokens, output_tokens)


def get_usage() -> dict:
    return dict(_usage())


def reset_usage():
    u = _usage()
    for k in u:
        u[k] = 0
