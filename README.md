# FitFindr 🛍️

A multi-tool AI agent that helps you find secondhand clothing and figure out how
to wear it. Describe what you want in natural language; FitFindr searches mock
listings, suggests an outfit using your wardrobe, and writes a shareable fit card
— branching on what each tool returns rather than running a fixed sequence.

## Setup

**macOS / Linux:**
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Windows:**
```bash
python -m venv .venv
source .venv/Scripts/activate
pip install -r requirements.txt
```

Set your Groq API key in a `.env` file (free key at [console.groq.com](https://console.groq.com)):
```
GROQ_API_KEY=your_key_here
```

## Run

```bash
python app.py          # launch the Gradio UI (open the URL shown in the terminal)
python agent.py        # CLI: runs a happy-path and a no-results interaction
pytest tests/          # run the tool + agent tests
```

The agent uses Groq's `llama-3.3-70b-versatile` for the two LLM-backed tools.

---

## Tool Inventory

Documented interfaces match the signatures in `tools.py`.

### `search_listings(description: str, size: str | None = None, max_price: float | None = None) -> list[dict]`
- **Inputs:** `description` (str) keywords; `size` (str | None) case-insensitive substring filter; `max_price` (float | None) inclusive price cap.
- **Output:** `list[dict]` of listing dicts (`id`, `title`, `description`, `category`, `style_tags`, `size`, `condition`, `price`, `colors`, `brand`, `platform`), sorted by keyword-overlap relevance. `[]` when nothing matches.
- **Purpose:** find candidate secondhand items from the mock dataset.

### `suggest_outfit(new_item: dict, wardrobe: dict) -> str`
- **Inputs:** `new_item` (dict) the selected listing; `wardrobe` (dict) with an `items` list (may be empty).
- **Output:** `str` — 1–2 outfit ideas naming specific wardrobe pieces, or general styling advice if the wardrobe is empty.
- **Purpose:** turn a found item into wearable outfit combinations.

### `create_fit_card(outfit: str, new_item: dict) -> str`
- **Inputs:** `outfit` (str) the suggestion text; `new_item` (dict) the listing.
- **Output:** `str` — a 2–4 sentence casual social caption (item name, price, platform mentioned once each). Temperature 1.0 → varies per call.
- **Purpose:** produce a shareable OOTD-style caption for the find.

---

## How the Planning Loop Works

`run_agent(query, wardrobe)` in `agent.py` makes decisions; it does **not** call
all three tools unconditionally.

1. **Guard** — blank query → set `error`, return.
2. **Parse** — `parse_query()` pulls `description`, `size`, `max_price` from the
   text with regex, stripping price/size phrases out of the description.
3. **Search + branch** — call `search_listings`. **If it returns `[]`**, set a
   specific `error` (naming which filter to relax) and **return early without
   calling `suggest_outfit` or `create_fit_card`.** Otherwise continue.
4. **Select** the top-ranked result.
5. **Suggest** an outfit, then **6. Card** it.

The branch at step 3 is the real decision point: an impossible query stops after
one tool call; a matching query runs the full pipeline.

## State Management

A single `session` dict holds everything for one interaction. Each tool writes a
named field that the next step reads — nothing is re-entered by the user:

`query` → `parsed` → `search_results` → `selected_item` → `outfit_suggestion` →
`fit_card` (plus `wardrobe` and `error`).

The exact dict at `search_results[0]` becomes `selected_item` and is passed
unchanged into both `suggest_outfit` and `create_fit_card` (a test asserts the
`is`-identity). `app.py` checks `session["error"]` first; if set, only the first
panel shows it.

---

## Interaction Walkthrough

**User query:** "I'm looking for a vintage graphic tee under $30. I mostly wear baggy jeans and chunky sneakers. What's out there and how would I style it?"

**Step 1 — `search_listings`**
- Input: `description="...vintage graphic tee...baggy jeans and chunky sneakers..."`, `size=None`, `max_price=30.0` (parsed from "under $30").
- Why: the user is asking what's available, so search runs first.
- Output: items ≤ $30 ranked by relevance; top result **"Y2K Baby Tee — Butterfly Print" — $18, depop, excellent**. Stored as `selected_item`.

**Step 2 — `suggest_outfit`**
- Input: the selected tee + the example wardrobe.
- Why: search returned a match, so the loop proceeds to styling.
- Output: e.g. *"Pair the Y2K baby tee with your baggy straight-leg jeans and chunky white sneakers for a casual streetwear look; or wide-leg khaki trousers + black combat boots for an eclectic grunge mix."*

**Step 3 — `create_fit_card`**
- Input: the outfit text + the selected tee.
- Why: a complete outfit exists, so it can be captioned.
- Output: e.g. *"just scored this y2k baby tee on depop for $18 and i'm obsessed 🦋 styling it with my baggy jeans + chunky sneakers for major 90s vibes."*

**Final output to user:** three panels — listing details, outfit idea, fit card.

---

## Error Handling and Fail Points

| Tool | Failure mode | Agent response |
|------|-------------|----------------|
| `search_listings` | No results match | Returns `[]`; the loop sets a specific error and stops. Tested example: query `"designer ballgown size XXS under $5"` → *"No listings matched 'designer ballgown'. Try removing the size filter (XXS) or the $5 price cap, or broadening your description."* `outfit_suggestion`/`fit_card` stay `None`. |
| `suggest_outfit` | Empty wardrobe | Switches to a general-advice prompt. Verified: `suggest_outfit(tee, get_empty_wardrobe())` returns staple-pairing advice, not an error. LLM/network error → deterministic fallback advice string. |
| `create_fit_card` | Missing/empty outfit | Detects empty/whitespace `outfit` before any LLM call and returns *"⚠️ Can't make a fit card — no outfit was provided…"*. Verified: `create_fit_card("", tee)`. LLM error → deterministic fallback caption. |

All three were triggered deliberately (see Milestone 5 commands) and produce
informative strings, never exceptions.

---

## Spec Reflection

**One way `planning.md` helped:** Writing the State Management table before
coding forced me to decide exactly which field each tool reads and writes. That
made `run_agent` almost mechanical to implement — the loop is just "fill the next
field" — and it surfaced the key invariant (the same `selected_item` dict flows
into both downstream tools), which I then locked down with an `is`-identity test.

**One divergence and why:** The spec implied the LLM might parse the query, but I
implemented `parse_query` with regex instead. Query parsing only needs price +
size + leftover keywords, and regex is deterministic, instant, free, and
testable — an LLM call there would add latency and a failure point for no real
gain. I kept the LLM where judgement actually matters (outfit + fit card).

---

## AI Usage

**Instance 1 — `search_listings` (Milestone 3).** Gave Claude the Tool 1 block
from `planning.md` (inputs, return fields, empty-result rule) plus
`utils/data_loader.py`, and asked it to implement filtering + keyword scoring on
top of `load_listings()`. I reviewed before trusting and changed two things: I
added stopword stripping so filler words ("looking", "size") don't inflate
scores, and I made the size filter match both directions (`"M"` in `"S/M"`) after
seeing dataset sizes like `"S/M"` and `"US 8.5"`. Verified with the
`test_search_*` cases.

**Instance 2 — planning loop (Milestone 4).** Gave Claude the Architecture
diagram + Planning Loop + State Management sections and asked it to write
`run_agent`. The first pass produced a generic "no results found" message; I
overrode it to name the specific filters to relax (size / price cap) using the
parsed values, because the spec's error-handling row required an actionable
response, not just a notice. Verified with `python agent.py` (both paths) and the
agent tests.

---

## Project Files

```
├── tools.py            # the 3 tools
├── agent.py            # run_agent planning loop + parse_query
├── app.py              # Gradio UI (handle_query)
├── tests/test_tools.py # pytest: tools + agent, incl. every failure mode
├── planning.md         # spec written before implementation
├── data/               # mock listings + wardrobe schema
└── utils/data_loader.py
```
