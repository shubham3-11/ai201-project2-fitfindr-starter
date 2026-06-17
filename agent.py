"""
agent.py

The FitFindr planning loop. Orchestrates the three tools in response to a
natural language user query, passing state between them via a session dict.

Complete tools.py and test each tool in isolation before implementing this file.

Usage (once implemented):
    from agent import run_agent
    from utils.data_loader import get_example_wardrobe

    result = run_agent(
        query="vintage graphic tee under $30, size M",
        wardrobe=get_example_wardrobe(),
    )
    print(result["fit_card"])
    print(result["error"])   # None on success
"""

import re

from tools import search_listings, suggest_outfit, create_fit_card


# ── query parsing ─────────────────────────────────────────────────────────────

# Known size tokens we look for in a query, longest/most-specific first so
# "size 8" wins over a bare "s", and "XL" wins over "L".
_SIZE_PATTERNS = [
    (r"\bus\s?(\d{1,2}(?:\.5)?)\b", lambda m: f"US {m.group(1)}"),
    (r"\bsize\s+(\d{1,2}(?:\.5)?)\b", lambda m: f"US {m.group(1)}"),
    (r"\bsize\s+(xxl|xl|xs|s|m|l)\b", lambda m: m.group(1).upper()),
    (r"\b(xxl|xl|xs)\b", lambda m: m.group(1).upper()),
    (r"\bsize\s+(small|medium|large)\b",
     lambda m: {"small": "S", "medium": "M", "large": "L"}[m.group(1)]),
    (r"\b(small|medium|large)\b",
     lambda m: {"small": "S", "medium": "M", "large": "L"}[m.group(1)]),
    (r"\bsize\s+([sml])\b", lambda m: m.group(1).upper()),
]


def parse_query(query: str) -> dict:
    """
    Extract description, size, and max_price from a natural language query.

    Uses regex (deterministic, no LLM cost) — see planning.md "Planning Loop".
        - max_price: first "$NN" or "under/below NN" number.
        - size:      first matching size pattern above.
        - description: the query with the price/size phrases stripped out,
                       so keyword search isn't polluted by "under $30" etc.
    """
    text = query.strip()
    low = text.lower()

    # Price: "$30", "under 30", "below $40", "less than 25".
    max_price = None
    price_match = re.search(
        r"(?:under|below|less than|max|<=?)\s*\$?\s*(\d+(?:\.\d+)?)|\$\s*(\d+(?:\.\d+)?)",
        low,
    )
    if price_match:
        raw = price_match.group(1) or price_match.group(2)
        max_price = float(raw)

    # Size: first pattern that matches.
    size = None
    for pat, render in _SIZE_PATTERNS:
        m = re.search(pat, low)
        if m:
            size = render(m)
            break

    # Description: strip the price phrase and explicit "size X" phrase so the
    # remaining words are pure search keywords.
    description = re.sub(
        r"(?:under|below|less than|max)\s*\$?\s*\d+(?:\.\d+)?|\$\s*\d+(?:\.\d+)?",
        " ", text, flags=re.IGNORECASE,
    )
    description = re.sub(
        r"\bsize\s+\S+", " ", description, flags=re.IGNORECASE,
    ).strip()
    description = re.sub(r"\s+", " ", description) or text

    return {"description": description, "size": size, "max_price": max_price}


# ── session state ─────────────────────────────────────────────────────────────

def _new_session(query: str, wardrobe: dict) -> dict:
    """
    Initialize and return a fresh session dict for one user interaction.

    The session dict is the single source of truth for everything that happens
    during a run — it stores the original query, parsed parameters, tool results,
    and any error that caused early termination.

    You may add fields to this dict as needed for your implementation.
    """
    return {
        "query": query,              # original user query
        "parsed": {},                # extracted description / size / max_price
        "search_results": [],        # list of matching listing dicts
        "selected_item": None,       # top result, passed into suggest_outfit
        "wardrobe": wardrobe,        # user's wardrobe dict
        "outfit_suggestion": None,   # string returned by suggest_outfit
        "fit_card": None,            # string returned by create_fit_card
        "error": None,               # set if the interaction ended early
    }


# ── planning loop ─────────────────────────────────────────────────────────────

def run_agent(query: str, wardrobe: dict) -> dict:
    """
    Main agent entry point. Runs the FitFindr planning loop for a single
    user interaction and returns the completed session dict.

    Args:
        query:    Natural language user request
                  (e.g., "vintage graphic tee under $30, size M")
        wardrobe: User's wardrobe dict — use get_example_wardrobe() or
                  get_empty_wardrobe() from utils/data_loader.py

    Returns:
        The session dict after the interaction completes. Check session["error"]
        first — if it is not None, the interaction ended early and the other
        output fields (outfit_suggestion, fit_card) will be None.

    TODO — implement this function using the planning loop you designed in planning.md:

        Step 1: Initialize the session with _new_session().

        Step 2: Parse the user's query to extract a description, size, and
                max_price. You can use regex, string splitting, or ask the LLM
                to parse it — document your choice in planning.md.
                Store the result in session["parsed"].

        Step 3: Call search_listings() with the parsed parameters.
                Store results in session["search_results"].
                If no results: set session["error"] to a helpful message and
                return the session early. Do NOT proceed to suggest_outfit
                with empty input.

        Step 4: Select the item to use (e.g., the top result).
                Store it in session["selected_item"].

        Step 5: Call suggest_outfit() with the selected item and wardrobe.
                Store the result in session["outfit_suggestion"].

        Step 6: Call create_fit_card() with the outfit suggestion and selected item.
                Store the result in session["fit_card"].

        Step 7: Return the session.

    Before writing code, complete the Planning Loop and State Management sections
    of planning.md — your implementation should match what you described there.
    """
    session = _new_session(query, wardrobe)

    # Step 1: guard empty input.
    if not query or not query.strip():
        session["error"] = "Tell me what you're looking for — e.g. 'vintage tee under $30'."
        return session

    # Step 2: parse the query into search parameters.
    session["parsed"] = parse_query(query)
    parsed = session["parsed"]

    # Step 3: search. Branch on the result — this is the core planning decision.
    session["search_results"] = search_listings(
        parsed["description"], parsed["size"], parsed["max_price"]
    )

    if not session["search_results"]:
        # No matches → terminate early. Do NOT call downstream tools with empty
        # input. Tell the user concretely what to relax.
        relax = []
        if parsed["size"]:
            relax.append(f"the size filter ({parsed['size']})")
        if parsed["max_price"] is not None:
            relax.append(f"the ${parsed['max_price']:g} price cap")
        hint = (
            f" Try removing {' or '.join(relax)}, or broadening your description."
            if relax else " Try different keywords."
        )
        session["error"] = (
            f"No listings matched \"{parsed['description']}\".{hint}"
        )
        return session

    # Step 4: select the top (most relevant) result for the rest of the flow.
    session["selected_item"] = session["search_results"][0]

    # Step 5: suggest an outfit using the selected item + wardrobe state.
    session["outfit_suggestion"] = suggest_outfit(
        session["selected_item"], session["wardrobe"]
    )

    # Step 6: turn the outfit into a shareable fit card.
    session["fit_card"] = create_fit_card(
        session["outfit_suggestion"], session["selected_item"]
    )

    # Step 7: done.
    return session


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from utils.data_loader import get_example_wardrobe, get_empty_wardrobe

    print("=== Happy path: graphic tee ===\n")
    session = run_agent(
        query="looking for a vintage graphic tee under $30",
        wardrobe=get_example_wardrobe(),
    )
    if session["error"]:
        print(f"Error: {session['error']}")
    else:
        print(f"Found: {session['selected_item']['title']}")
        print(f"\nOutfit: {session['outfit_suggestion']}")
        print(f"\nFit card: {session['fit_card']}")

    print("\n\n=== No-results path ===\n")
    session2 = run_agent(
        query="designer ballgown size XXS under $5",
        wardrobe=get_example_wardrobe(),
    )
    print(f"Error message: {session2['error']}")
