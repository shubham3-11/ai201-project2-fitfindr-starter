# FitFindr — planning.md

> Spec written before implementation. Used to direct the AI tool (Claude) when generating each function.

---

## Tools

### Tool 1: search_listings

**What it does:**
Searches the mock listings dataset for secondhand items matching a keyword
description, optionally filtered by size and a price ceiling, and returns them
ranked by relevance.

**Input parameters:**
- `description` (str): free-text keywords describing the wanted item (e.g. `"vintage graphic tee"`). Tokenized and matched against each listing's title, description, category, style_tags, colors, and brand.
- `size` (str | None): size to filter by, or `None` to skip. Case-insensitive substring match in either direction (`"M"` matches `"S/M"`; `"US 8"` matches `"US 8"`).
- `max_price` (float | None): inclusive price ceiling, or `None` to skip price filtering.

**What it returns:**
`list[dict]` of listing dicts, highest relevance first. Each dict has: `id`,
`title`, `description`, `category`, `style_tags` (list), `size`, `condition`,
`price` (float), `colors` (list), `brand`, `platform`. Relevance = count of
query tokens that appear in the listing's searchable fields. Zero-score
listings are dropped. Returns `[]` when nothing matches.

**What happens if it fails or returns nothing:**
Returns an empty list `[]` — never raises. If the data file is missing/corrupt
it also degrades to `[]`. The planning loop detects the empty list, sets a
helpful `session["error"]`, and stops before calling downstream tools.

---

### Tool 2: suggest_outfit

**What it does:**
Given a thrifted item and the user's wardrobe, asks the LLM to propose 1–2
complete outfits referencing specific wardrobe pieces by name.

**Input parameters:**
- `new_item` (dict): a listing dict (the selected search result).
- `wardrobe` (dict): a wardrobe dict with an `items` key (list of item dicts with `name`, `category`, `colors`, `style_tags`, `notes`). May be empty.

**What it returns:**
A non-empty `str`. With a populated wardrobe: 3–5 sentences naming specific
closet items. With an empty wardrobe: general styling advice (what pairs well,
the vibe, 1–2 staple-based outfit ideas).

**What happens if it fails or returns nothing:**
Empty wardrobe → general-advice prompt path (still returns useful text).
Invalid/empty `new_item` → returns a short ask-for-item message. LLM/network
error → returns a deterministic fallback styling string. Never raises, never
returns `""`.

---

### Tool 3: create_fit_card

**What it does:**
Turns an outfit suggestion + the item into a short, shareable social-media
caption (OOTD/thrift-haul style).

**Input parameters:**
- `outfit` (str): the outfit suggestion string from `suggest_outfit()`.
- `new_item` (dict): the listing dict for the thrifted item (used for title, price, platform).

**What it returns:**
A 2–4 sentence caption `str` mentioning the item name, price, and platform once
each. Generated at temperature 1.0 so repeated calls on the same input differ.

**What happens if it fails or returns nothing:**
Empty/whitespace `outfit` → returns a descriptive `⚠️` error string (does NOT
call the LLM). Invalid `new_item` → returns an error string. LLM/network error
→ deterministic fallback caption. Never raises.

---

### Additional Tools (if any)

None — three required tools only.

---

## Planning Loop

The agent does not run a fixed sequence; it branches on what `search_listings`
returns. `run_agent(query, wardrobe)` logic:

1. **Guard input** — if `query` is blank, set `session["error"]` and return.
2. **Parse** — `parse_query()` extracts `description`, `size`, `max_price` via
   regex (price from `$NN` / `under NN`; size from size patterns), strips those
   phrases from the description, and stores the result in `session["parsed"]`.
3. **Search** — call `search_listings(description, size, max_price)`; store in
   `session["search_results"]`.
   - **Branch A — `results == []`:** build a message naming which filters to
     relax (size, price cap, keywords), set `session["error"]`, and **return
     early. Do not call `suggest_outfit` or `create_fit_card`.**
   - **Branch B — `results` non-empty:** continue.
4. **Select** — `session["selected_item"] = search_results[0]` (top relevance).
5. **Suggest** — `suggest_outfit(selected_item, wardrobe)` →
   `session["outfit_suggestion"]`.
6. **Card** — `create_fit_card(outfit_suggestion, selected_item)` →
   `session["fit_card"]`.
7. **Return** the session.

The loop is "done" when it either returns early at step 3A or completes the card
at step 6. Behavior differs by input: an impossible query terminates after one
tool call with an error; a matching query runs all three.

---

## State Management

A single `session` dict (built by `_new_session`) is the source of truth for one
interaction. Each tool's output is written to a named field and read by the next
step — the user never re-enters anything:

| Field | Written by | Read by |
|-------|-----------|---------|
| `query` | caller | step 1 / parse |
| `parsed` | step 2 (`parse_query`) | step 3 search |
| `search_results` | step 3 (`search_listings`) | step 3 branch / step 4 |
| `selected_item` | step 4 | `suggest_outfit`, `create_fit_card` |
| `wardrobe` | caller | `suggest_outfit` |
| `outfit_suggestion` | step 5 (`suggest_outfit`) | `create_fit_card` |
| `fit_card` | step 6 (`create_fit_card`) | UI |
| `error` | step 1 or 3A | UI (checked first) |

The exact dict in `search_results[0]` is the same object passed into
`suggest_outfit` and `create_fit_card` (verified by an `is` assertion in tests).
The UI (`app.py`) checks `session["error"]` first; if set, only the first panel
shows the message and the other two stay blank.

---

## Error Handling

| Tool | Failure mode | Agent response |
|------|-------------|----------------|
| search_listings | No results match the query | Returns `[]`; planning loop sets `error` naming which filter to relax (e.g. *"No listings matched 'designer ballgown'. Try removing the $5 price cap, or broadening your description."*) and stops before downstream tools. |
| suggest_outfit | Wardrobe is empty | Switches to a general-styling-advice prompt and returns useful text (staple pairings + 1–2 outfit ideas) instead of failing. LLM error → deterministic fallback advice. |
| create_fit_card | Outfit input is missing or incomplete | Detects empty/whitespace `outfit` and returns a descriptive `⚠️` error string without calling the LLM. LLM error → deterministic fallback caption. |

---

## Architecture

```
User query + wardrobe choice
        │
        ▼
   parse_query()  ──► session["parsed"] = {description, size, max_price}
        │
        ▼
Planning Loop (run_agent) ─────────────────────────────────────────────┐
        │                                                               │
        ├─► search_listings(description, size, max_price)               │
        │       │ results == []                                         │
        │       ├──► session["error"] = "No listings matched…" ─► return┤ (error
        │       │                                                       │  branch)
        │       │ results == [item, ...]                                │
        │       ▼                                                       │
        │   session["selected_item"] = results[0]                       │
        │       │                                                       │
        ├─► suggest_outfit(selected_item, wardrobe)                     │
        │       │  (empty wardrobe → general advice path)               │
        │   session["outfit_suggestion"] = "..."                        │
        │       │                                                       │
        └─► create_fit_card(outfit_suggestion, selected_item)           │
                │  (empty outfit → error string, no LLM)                │
            session["fit_card"] = "..."                                 │
                │                                                       │
                ▼                                                       ▼
            Return session ◄────────────────────────────────── Return session
                │
                ▼
   app.py handle_query maps session → 3 Gradio panels
   (error → panel 1 only; success → listing / outfit / fit card)
```

State store: the `session` dict (see State Management) is shared across every
node above — each tool reads earlier fields and writes its own.

---

## AI Tool Plan

**Milestone 3 — Individual tool implementations:**
Tool used: **Claude (Claude Code)**.
- `search_listings`: gave Claude the Tool 1 spec (inputs, return fields, empty-result rule) + `utils/data_loader.py` and asked it to implement filtering+keyword scoring on top of `load_listings()`. Verified before trusting: all three params filter correctly, zero-score items dropped, empty list on no match — checked with `pytest tests/test_tools.py::test_search_*`.
- `suggest_outfit` / `create_fit_card`: gave Claude the Tool 2/3 spec blocks and the Groq model name. Required: empty-wardrobe branch, empty-outfit guard *before* any LLM call, temperature 1.0 on the card for variation, deterministic fallbacks on LLM error. Verified with the failure-mode tests and by running the card twice to confirm output differs.

**Milestone 4 — Planning loop and state management:**
Gave Claude the **Architecture diagram** + **Planning Loop** + **State
Management** sections and asked it to implement `run_agent()` and `parse_query()`.
Reviewed the generated loop before running: confirmed it (a) branches on the
`search_listings` result instead of calling all tools unconditionally, (b)
writes every value into the `session` dict, (c) returns early on empty results.
Verified with `python agent.py` (happy + no-results paths) and the agent tests.

---

## A Complete Interaction (Step by Step)

**Example user query:** "I'm looking for a vintage graphic tee under $30. I mostly wear baggy jeans and chunky sneakers. What's out there and how would I style it?"

**Step 1 — Parse:**
`parse_query()` extracts `max_price = 30.0`, `size = None`, and a cleaned
`description = "I'm looking for a vintage graphic tee . I mostly wear baggy jeans
and chunky sneakers. What's out there and how would I style it?"` (price phrase
stripped). Stored in `session["parsed"]`.

**Step 2 — Search:**
`search_listings(description, None, 30.0)` filters to items ≤ $30 and scores
keyword overlap. Returns matches ranked by relevance; top result is the
**"Y2K Baby Tee — Butterfly Print" ($18, depop, excellent)**. Stored in
`session["search_results"]`; `session["selected_item"] = results[0]`.

**Step 3 — Suggest outfit:**
`suggest_outfit(selected_item, example_wardrobe)` → e.g. *"Pair the Y2K baby tee
with your baggy straight-leg jeans and chunky white sneakers for a casual
streetwear look; or with wide-leg khaki trousers and black combat boots for an
eclectic grunge mix."* Stored in `session["outfit_suggestion"]`.

**Step 4 — Fit card:**
`create_fit_card(outfit_suggestion, selected_item)` → e.g. *"just scored this
adorable y2k baby tee on depop for $18 and i'm obsessed 🦋 pairing it with my
baggy jeans + chunky sneakers for major 90s vibes."* Stored in
`session["fit_card"]`.

**Final output to user:**
Three panels — the listing details (title, price, platform, size, tags,
description), the outfit suggestion, and the shareable fit card.

**Error path:** the query "designer ballgown size XXS under $5" returns `[]` from
`search_listings`, so the agent sets `session["error"]` ("No listings matched
'designer ballgown'. Try removing the size filter (XXS) or the $5 price cap…")
and stops — `outfit_suggestion` and `fit_card` stay `None`.
