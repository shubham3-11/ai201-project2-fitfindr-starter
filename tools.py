"""
tools.py

The three required FitFindr tools. Each tool is a standalone function that
can be called and tested independently before being wired into the agent loop.

Tools:
    search_listings(description, size, max_price)  → list[dict]
    suggest_outfit(new_item, wardrobe)              → str
    create_fit_card(outfit, new_item)               → str
"""

import os
import re

from dotenv import load_dotenv
from groq import Groq

from utils.data_loader import load_listings

load_dotenv()

_MODEL = "llama-3.3-70b-versatile"

# Words that carry no search signal — dropped before keyword scoring.
_STOPWORDS = {
    "a", "an", "the", "for", "with", "and", "or", "of", "to", "in", "on",
    "under", "over", "size", "im", "i'm", "looking", "want", "need", "some",
    "my", "me", "that", "this", "really", "very", "kind", "sort", "something",
}


# ── Groq client ───────────────────────────────────────────────────────────────

def _get_groq_client():
    """Initialize and return a Groq client using GROQ_API_KEY from .env."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError(
            "GROQ_API_KEY not set. Add it to a .env file in the project root."
        )
    return Groq(api_key=api_key)


def _tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumerics, drop stopwords and 1-char tokens."""
    tokens = re.findall(r"[a-z0-9']+", text.lower())
    return [t for t in tokens if t not in _STOPWORDS and len(t) > 1]


def _chat(messages: list[dict], temperature: float, max_tokens: int = 400) -> str:
    """Single LLM call. Raises on transport/API failure — callers guard this."""
    client = _get_groq_client()
    resp = client.chat.completions.create(
        model=_MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content.strip()


# ── Tool 1: search_listings ───────────────────────────────────────────────────

def search_listings(
    description: str,
    size: str | None = None,
    max_price: float | None = None,
) -> list[dict]:
    """
    Search the mock listings dataset for items matching the description,
    optional size, and optional price ceiling.

    Args:
        description: Keywords describing what the user is looking for.
        size:        Size string to filter by, or None to skip. Case-insensitive
                     substring match (e.g., "M" matches "S/M").
        max_price:   Maximum price (inclusive), or None to skip price filtering.

    Returns:
        List of matching listing dicts sorted by relevance (best first).
        Empty list if nothing matches — never raises.
    """
    try:
        listings = load_listings()
    except (OSError, ValueError):
        # Data file missing or corrupt — degrade to no results rather than crash.
        return []

    query_tokens = _tokenize(description or "")
    size_norm = size.strip().lower() if size else None

    scored: list[tuple[int, dict]] = []
    for item in listings:
        # 1. Price filter.
        if max_price is not None and item.get("price", float("inf")) > max_price:
            continue

        # 2. Size filter — case-insensitive substring either direction.
        if size_norm:
            item_size = str(item.get("size", "")).lower()
            if size_norm not in item_size and item_size not in size_norm:
                continue

        # 3. Keyword overlap score across the searchable fields.
        haystack = " ".join([
            item.get("title", ""),
            item.get("description", ""),
            item.get("category", ""),
            " ".join(item.get("style_tags", [])),
            " ".join(item.get("colors", [])),
            str(item.get("brand") or ""),
        ]).lower()
        hay_tokens = set(_tokenize(haystack))
        score = sum(1 for t in query_tokens if t in hay_tokens)

        # 4. Drop zero-relevance items. If no query tokens at all (e.g. blank
        #    description), keep items that passed the structured filters.
        if query_tokens and score == 0:
            continue

        scored.append((score, item))

    # 5. Sort by score desc; ties keep dataset order (stable sort).
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored]


# ── Tool 2: suggest_outfit ────────────────────────────────────────────────────

def suggest_outfit(new_item: dict, wardrobe: dict) -> str:
    """
    Given a thrifted item and the user's wardrobe, suggest 1–2 complete outfits.

    Args:
        new_item: A listing dict (the item the user is considering).
        wardrobe: A wardrobe dict with an 'items' key (list of item dicts).
                  May be empty — handled gracefully.

    Returns:
        Non-empty string of outfit suggestions. Empty wardrobe → general
        styling advice. Never raises.
    """
    if not isinstance(new_item, dict) or not new_item:
        return "I need a valid item before I can suggest an outfit."

    title = new_item.get("title", "the item")
    item_desc = new_item.get("description", "")
    tags = ", ".join(new_item.get("style_tags", [])) or "n/a"
    colors = ", ".join(new_item.get("colors", [])) or "n/a"

    items = (wardrobe or {}).get("items", [])

    if not items:
        prompt = (
            f"A shopper is considering this secondhand piece:\n"
            f"- Title: {title}\n- Description: {item_desc}\n"
            f"- Style tags: {tags}\n- Colors: {colors}\n\n"
            "They have NOT entered any wardrobe yet. Give general styling advice: "
            "what kinds of pieces pair well with this, what vibe it suits, and 1–2 "
            "example outfit ideas using common staples. Keep it to 3–4 sentences, "
            "concrete and friendly."
        )
    else:
        closet = "\n".join(
            f"- {it.get('name', '?')} ({it.get('category', '?')}; "
            f"{', '.join(it.get('style_tags', []))})"
            for it in items
        )
        prompt = (
            f"A shopper is considering this secondhand piece:\n"
            f"- Title: {title}\n- Description: {item_desc}\n"
            f"- Style tags: {tags}\n- Colors: {colors}\n\n"
            f"Their current wardrobe:\n{closet}\n\n"
            "Suggest 1–2 complete outfits that combine the new piece with SPECIFIC "
            "named items from their wardrobe. Reference items by name. Keep it to "
            "3–5 sentences, practical and stylish."
        )

    try:
        out = _chat(
            [
                {"role": "system", "content": "You are a sharp, friendly personal stylist."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
        )
        return out or _fallback_outfit(title, bool(items))
    except Exception:
        # LLM unavailable — return useful deterministic advice rather than crash.
        return _fallback_outfit(title, bool(items))


def _fallback_outfit(title: str, has_wardrobe: bool) -> str:
    """Deterministic styling advice used if the LLM call fails."""
    if has_wardrobe:
        return (
            f"Couldn't reach the styling model just now, but {title} is versatile — "
            "pair it with a contrasting bottom and a neutral shoe from your closet, "
            "then add one accessory to pull the look together."
        )
    return (
        f"Couldn't reach the styling model just now, but {title} works well with "
        "simple staples: a fitted layer, a relaxed bottom, and clean shoes. Build "
        "around its main color and keep the rest neutral."
    )


# ── Tool 3: create_fit_card ───────────────────────────────────────────────────

def create_fit_card(outfit: str, new_item: dict) -> str:
    """
    Generate a short, shareable outfit caption for the thrifted find.

    Args:
        outfit:   The outfit suggestion string from suggest_outfit().
        new_item: The listing dict for the thrifted item.

    Returns:
        A 2–4 sentence caption string. If outfit is empty/missing, returns a
        descriptive error message string — never raises.
    """
    if not outfit or not outfit.strip():
        return (
            "⚠️ Can't make a fit card — no outfit was provided. "
            "Find an item and generate an outfit suggestion first."
        )
    if not isinstance(new_item, dict) or not new_item:
        return "⚠️ Can't make a fit card — the item details are missing."

    title = new_item.get("title", "this piece")
    price = new_item.get("price")
    price_str = f"${price:g}" if isinstance(price, (int, float)) else "a steal"
    platform = new_item.get("platform", "secondhand")

    prompt = (
        f"Write a short, casual social-media caption (like a real OOTD/thrift "
        f"post — NOT a product description) for this find.\n\n"
        f"Item: {title}\nPrice: {price_str}\nPlatform: {platform}\n"
        f"Outfit it's styled in: {outfit}\n\n"
        "Rules: 2–4 sentences. Mention the item name, price, and platform once "
        "each, naturally. Capture the vibe in specific terms. Sound authentic and "
        "a little excited. Lowercase-casual is fine. Emojis ok but sparing."
    )

    try:
        # Higher temperature so repeated calls vary.
        out = _chat(
            [
                {"role": "system", "content": "You write punchy, authentic thrift-haul captions."},
                {"role": "user", "content": prompt},
            ],
            temperature=1.0,
            max_tokens=160,
        )
        return out or _fallback_card(title, price_str, platform)
    except Exception:
        return _fallback_card(title, price_str, platform)


def _fallback_card(title: str, price_str: str, platform: str) -> str:
    """Deterministic caption used if the LLM call fails."""
    return (
        f"scored this {title} for {price_str} on {platform} and i'm obsessed 🖤 "
        "already planning the fit — full look coming soon."
    )
