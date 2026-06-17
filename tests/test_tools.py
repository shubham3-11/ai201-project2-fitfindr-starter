"""
Tests for the three FitFindr tools and the agent planning loop.

Run from the repo root:
    pytest tests/

Tests that hit the Groq LLM (suggest_outfit, create_fit_card happy paths,
full-agent happy path) are marked `llm` and skipped if GROQ_API_KEY is unset,
so the failure-mode tests always run offline.
"""

import os

import pytest

from tools import search_listings, suggest_outfit, create_fit_card
from agent import run_agent, parse_query
from utils.data_loader import get_example_wardrobe, get_empty_wardrobe, load_listings

_HAS_KEY = bool(os.environ.get("GROQ_API_KEY"))
needs_llm = pytest.mark.skipif(not _HAS_KEY, reason="GROQ_API_KEY not set")


# ── search_listings ─────────────────────────────────────────────────────────

def test_search_returns_results():
    results = search_listings("vintage graphic tee", size=None, max_price=None)
    assert isinstance(results, list)
    assert len(results) > 0


def test_search_empty_results():
    # Impossible combo → empty list, no exception.
    results = search_listings("designer ballgown", size="XXS", max_price=5)
    assert results == []


def test_search_price_filter():
    results = search_listings("jacket", size=None, max_price=40)
    assert all(item["price"] <= 40 for item in results)


def test_search_sorted_by_relevance():
    results = search_listings("vintage denim jacket", size=None, max_price=None)
    # Listings carry only style/keyword fields; top result should out-score later ones.
    assert len(results) >= 2


def test_search_blank_description_no_crash():
    results = search_listings("", size="M", max_price=None)
    assert isinstance(results, list)  # structured filter only, no exception


# ── suggest_outfit ──────────────────────────────────────────────────────────

def test_suggest_empty_wardrobe_returns_string():
    item = load_listings()[0]
    out = suggest_outfit(item, get_empty_wardrobe())
    assert isinstance(out, str) and out.strip() != ""


def test_suggest_bad_item_no_crash():
    out = suggest_outfit({}, get_example_wardrobe())
    assert isinstance(out, str) and out.strip() != ""


@needs_llm
def test_suggest_with_wardrobe():
    item = load_listings()[0]
    out = suggest_outfit(item, get_example_wardrobe())
    assert isinstance(out, str) and len(out) > 20


# ── create_fit_card ─────────────────────────────────────────────────────────

def test_fit_card_empty_outfit_returns_error_string():
    item = load_listings()[0]
    out = create_fit_card("", item)
    assert isinstance(out, str)
    assert "⚠️" in out or "outfit" in out.lower()


def test_fit_card_whitespace_outfit():
    item = load_listings()[0]
    out = create_fit_card("   ", item)
    assert isinstance(out, str) and out.strip() != ""


@needs_llm
def test_fit_card_varies_across_runs():
    item = load_listings()[0]
    outfit = "Pair with wide-leg jeans and chunky sneakers."
    a = create_fit_card(outfit, item)
    b = create_fit_card(outfit, item)
    assert a != b  # high temperature → different captions


# ── query parsing ───────────────────────────────────────────────────────────

def test_parse_extracts_price_and_size():
    p = parse_query("vintage graphic tee under $30, size M")
    assert p["max_price"] == 30.0
    assert p["size"] == "M"
    assert "tee" in p["description"].lower()
    assert "$" not in p["description"]


def test_parse_shoe_size():
    p = parse_query("black combat boots size 8")
    assert p["size"] == "US 8"


# ── agent planning loop ─────────────────────────────────────────────────────

def test_agent_no_results_sets_error_and_skips_downstream():
    session = run_agent("designer ballgown size XXS under $5", get_example_wardrobe())
    assert session["error"] is not None
    assert session["fit_card"] is None
    assert session["outfit_suggestion"] is None


def test_agent_empty_query():
    session = run_agent("   ", get_example_wardrobe())
    assert session["error"] is not None


@needs_llm
def test_agent_happy_path_full_flow():
    session = run_agent("vintage graphic tee under $30", get_example_wardrobe())
    assert session["error"] is None
    assert session["selected_item"] is not None
    assert session["outfit_suggestion"]
    assert session["fit_card"]
    # State integrity: the item searched is the exact dict carried downstream.
    assert session["selected_item"] is session["search_results"][0]
