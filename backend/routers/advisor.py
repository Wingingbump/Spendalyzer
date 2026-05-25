import os
import json
import re
import time
import threading
from datetime import date, timedelta

import anthropic
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.dependencies import get_current_user
from backend.limiter import limiter
from fastapi import Request
from core.db import (
    get_conn, get_user_conn,
    list_goals, get_goal, create_goal, update_goal, delete_goal,
    list_advice, store_advice, update_advice_reaction,
    retrieve_relevant_memories, store_memory,
    get_user_financial_profile, upsert_user_financial_profile,
    list_financial_snapshots, create_financial_snapshot,
    get_user_profile,
)
from core.embeddings import get_query_embedding, get_embedding
from core.insights import load_data, get_spending, spending_by_category, spending_by_month

router = APIRouter(prefix="/advisor", tags=["advisor"])

_anthropic = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

ADVISOR_MODEL = "claude-sonnet-4-6"
EXTRACT_MODEL = "claude-haiku-4-5-20251001"  # cheap model for memory extraction


# ── Request / Response models ─────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[dict] = Field(default_factory=list, max_length=20)

class ChatResponse(BaseModel):
    response: str
    advice_id: int
    compliance_flags: list[str]
    actions: list[dict] = []

class GoalCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    type: str = Field(default="other")
    target_amount: float | None = None
    current_amount: float = 0
    deadline: str | None = None  # ISO date string
    priority: int = Field(default=3, ge=1, le=5)
    notes: str | None = None

class GoalUpdate(BaseModel):
    title: str | None = None
    type: str | None = None
    target_amount: float | None = None
    current_amount: float | None = None
    deadline: str | None = None
    priority: int | None = Field(default=None, ge=1, le=5)
    status: str | None = None
    notes: str | None = None

class ReactionUpdate(BaseModel):
    reaction: str = Field(pattern="^(followed|ignored|partial|unknown)$")
    outcome_notes: str | None = None

class OnboardRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[dict] = Field(default_factory=list, max_length=20)

class OnboardResponse(BaseModel):
    response: str
    completed: bool
    options: list[str] = []

class ProfileUpdate(BaseModel):
    life_stage: str | None = None
    risk_tolerance: str | None = None
    income_estimate: float | None = None
    communication_style: str | None = None


# ── Onboarding ────────────────────────────────────────────────────────────────

_SUGGEST_OPTIONS_TOOL = {
    "name": "suggest_options",
    "description": (
        "Attach 2-5 selectable options to your response whenever you ask a question "
        "that has common, predictable answers. The user can tap an option or type their own. "
        "Call this alongside your text response — not instead of it."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "2-5 short, specific options. Keep each under 40 characters.",
            },
        },
        "required": ["options"],
    },
}

_ONBOARD_TOOL = {
    "name": "complete_onboarding",
    "description": (
        "Call this when you have gathered enough information to build the user's financial "
        "profile. Use it after learning their life stage, rough income, main financial goals, "
        "and comfort with risk. Typically after 4-6 exchanges — don't wait for perfect info."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "life_stage": {
                "type": "string",
                "enum": ["student", "early_career", "mid_career", "pre_retirement", "retirement"],
            },
            "income_estimate": {
                "type": "number",
                "description": "Annual income in dollars. Use midpoint of any range mentioned.",
            },
            "risk_tolerance": {
                "type": "string",
                "enum": ["conservative", "moderate", "aggressive"],
            },
            "communication_style": {
                "type": "string",
                "enum": ["direct", "detailed", "encouraging", "analytical"],
            },
            "goals": {
                "type": "array",
                "description": "Financial goals the user mentioned",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "type": {
                            "type": "string",
                            "enum": ["emergency_fund", "house", "retirement", "debt_payoff",
                                     "investment", "travel", "education", "other"],
                        },
                        "target_amount": {"type": "number"},
                        "deadline": {"type": "string", "description": "ISO date YYYY-MM-DD"},
                    },
                    "required": ["title", "type"],
                },
            },
            "closing_message": {
                "type": "string",
                "description": (
                    "Warm closing message to the user. Briefly reflect back what you learned "
                    "about them, then let them know you're ready to help."
                ),
            },
        },
        "required": ["life_stage", "income_estimate", "risk_tolerance", "closing_message"],
    },
}

_ONBOARD_SYSTEM = f"""You are onboarding a new user to their personal AI financial advisor. Your goal is a warm, natural conversation to learn enough about their financial life to give them personalized advice going forward.

Learn these things conversationally — one or two topics at a time, never a bulleted list:
1. Life situation: age range or life stage, employment status, any dependents
2. Rough income (a range is fine — "$50-70k" is enough, you don't need precision)
3. Top 1-3 financial goals and rough timelines
4. Comfort with financial risk (e.g. how would they feel if their investments dropped 20%?)
5. How they prefer to get information (brief and direct, detailed explanations, or something else)

Guidelines:
- Ask one or two questions at a time, not five at once
- Reflect what you hear back — show you're listening
- Be warm and conversational, not clinical
- After 4-6 exchanges once you have enough, call the complete_onboarding tool
- Ranges and approximations are fine — don't push for exact numbers

For almost every question you ask, also call suggest_options with 2-5 short answer choices that cover the most common responses. Examples:
- Life stage question → ["Student", "Early career (20s–30s)", "Mid career (30s–50s)", "Pre-retirement", "Retired"]
- Income question → ["Under $40k", "$40–70k", "$70–100k", "$100–150k", "Over $150k"]
- Goals question → ["Build emergency fund", "Buy a house", "Pay off debt", "Save for retirement", "Grow investments"]
- Risk question → ["Play it safe — stability over growth", "Balanced — some risk is okay", "Aggressive — maximize long-term growth"]
- Style question → ["Keep it brief and direct", "Walk me through the details", "Be encouraging", "Give me the numbers"]
The user can always type their own answer instead — options are shortcuts, not constraints.

Today's date: {date.today().isoformat()}"""


# ── Compliance check ──────────────────────────────────────────────────────────

# Patterns that cross the RIA line — specific investment advice
_RED_FLAG_PATTERNS = [
    r"\byou should (buy|sell|purchase|invest in)\b.{0,60}(stock|etf|fund|crypto|bond|ticker|share)",
    r"\b(buy|sell)\s+[A-Z]{1,5}\b",                # ticker symbols like "buy AAPL"
    r"\bput\s+\d+\s*%\s+(in|into)\s+(equities|stocks|bonds|crypto)",
    r"\bguaranteed\s+(return|gain|profit|yield)",
    r"\byou (will|can) make\b.{0,40}(profit|return|gain)",
]

# Patterns that need a disclaimer — only when giving SPECIFIC advice, not just mentioning the topic
_YELLOW_FLAG_PATTERNS = [
    r"\b(you should (file|amend|claim|report)|this (qualifies|counts) as a deduction|your tax (liability|bracket) is)\b",
    r"\b(you (need|should get) (a will|an attorney|legal counsel|estate planning))\b",
    r"\b(you should (buy|get|cancel) (life insurance|disability insurance|an annuity))\b",
]

_DISCLAIMER = (
    "\n\n*This is general financial education, not personalized investment advice. "
    "For decisions specific to your situation, consider speaking with a CFP or CPA.*"
)


def _compliance_check(text: str) -> tuple[str, list[str]]:
    """Returns (possibly amended text, list of flag descriptions)."""
    flags = []

    for pattern in _RED_FLAG_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            flags.append(f"red:{pattern[:40]}")

    for pattern in _YELLOW_FLAG_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            flags.append(f"yellow:{pattern[:40]}")

    # Yellow flags just get a disclaimer appended
    if flags and not any(f.startswith("red:") for f in flags):
        if _DISCLAIMER not in text:
            text = text + _DISCLAIMER

    # Red flags: ask Claude to rewrite the offending response
    if any(f.startswith("red:") for f in flags):
        try:
            rewrite = _anthropic.messages.create(
                model=EXTRACT_MODEL,
                max_tokens=1024,
                system=(
                    "You are a compliance editor for a financial coaching app. "
                    "Rewrite the following response to remove any specific investment advice "
                    "(e.g. 'buy X', 'sell Y', specific ticker recommendations, guaranteed return claims). "
                    "Replace with general principles and suggest consulting a CFP. "
                    "Preserve all other useful financial education content. "
                    "Return only the rewritten response, no commentary."
                ),
                messages=[{"role": "user", "content": text}],
            )
            text = rewrite.content[0].text
        except Exception:
            # If rewrite fails, append a hard disclaimer instead
            text = text + _DISCLAIMER

    return text, flags


# ── Memory extraction ─────────────────────────────────────────────────────────

def _extract_and_store_memories(user_id: int, user_message: str, advisor_response: str):
    """Ask Haiku to extract memory-worthy items from the exchange, then store them."""
    try:
        result = _anthropic.messages.create(
            model=EXTRACT_MODEL,
            max_tokens=512,
            system=(
                "You extract memory-worthy facts from financial conversations. "
                "Given a user message and advisor response, output a JSON array of 0-3 memories. "
                "Only extract facts that are worth remembering long-term: goals, concerns, preferences, "
                "life events, behavioral patterns, or important financial context. "
                "Skip generic exchanges. Each memory: "
                '{"content": "...", "type": "goal|concern|preference|event|behavior|context", "importance": 1-5}. '
                "Respond with only the JSON array, nothing else."
            ),
            messages=[{
                "role": "user",
                "content": f"User: {user_message}\n\nAdvisor: {advisor_response}"
            }],
        )
        raw = result.content[0].text.strip()
        memories = json.loads(raw)
        if not isinstance(memories, list):
            return
        for m in memories[:3]:  # cap at 3 per exchange
            content = m.get("content", "").strip()
            if not content:
                continue
            embedding = get_embedding(content)
            if _is_duplicate_memory(user_id, embedding):
                continue  # near-duplicate already stored — skip
            store_memory(
                user_id=user_id,
                content=content,
                memory_type=m.get("type", "context"),
                importance=int(m.get("importance", 3)),
                source="conversation",
                embedding=embedding,
            )
    except Exception as e:
        # Memory extraction is best-effort — never block the response
        print(f"[advisor] memory extraction failed: {e}")


# ── Budget + recurring helpers ────────────────────────────────────────────────

def _fetch_budgets_with_spend(user_id: int, df: pd.DataFrame) -> list[dict]:
    """Return budget rows with current-month spend overlaid."""
    with get_user_conn(user_id) as conn:
        rows = conn.execute(
            "SELECT category, amount, period FROM budgets WHERE user_id = %s ORDER BY category",
            (user_id,)
        ).fetchall()
    budgets = [dict(r) for r in rows]
    if not budgets:
        return []

    cat_spend: dict[str, float] = {}
    if not df.empty and "is_transfer" in df.columns:
        today = date.today()
        month_start = pd.Timestamp(today.replace(day=1))
        month_df = df[
            (df["date"] >= month_start) &
            (~df.get("is_transfer", pd.Series(False, index=df.index)).fillna(False)) &
            (~df.get("is_duplicate", pd.Series(False, index=df.index)).fillna(False)) &
            (df["type"] == "debit")
        ]
        if "category" in month_df.columns:
            cat_spend = month_df.groupby("category")["amount"].sum().to_dict()

    for b in budgets:
        b["spent"] = round(float(cat_spend.get(b["category"], 0.0)), 2)
        b["amount"] = float(b["amount"])
    return budgets


_FREQ_RANGES = [
    ("weekly",     5,   9),
    ("biweekly",  12,  16),
    ("monthly",   25,  35),
    ("quarterly", 85, 100),
    ("annual",   330, 390),
]


def _detect_recurring_simple(df: pd.DataFrame) -> list[dict]:
    """Detect recurring transactions from df. Returns top-20 by amount."""
    if df.empty:
        return []
    clean = df[
        (~df.get("is_transfer", pd.Series(False, index=df.index)).fillna(False)) &
        (~df.get("is_duplicate", pd.Series(False, index=df.index)).fillna(False)) &
        (df["type"] == "debit")
    ].copy()
    if clean.empty:
        return []

    clean["_key"] = clean.apply(
        lambda r: (r.get("merchant_normalized") or "").strip() or str(r["name"]),
        axis=1,
    )
    results = []
    for key, group in clean.groupby("_key"):
        if len(group) < 2:
            continue
        group = group.sort_values("date")
        amounts = group["amount"].tolist()
        sorted_amt = sorted(amounts)
        median_amt = sorted_amt[len(sorted_amt) // 2]
        if median_amt <= 0:
            continue
        if any(abs(a - median_amt) / median_amt > 0.15 for a in amounts):
            continue
        dates = group["date"].tolist()
        diffs = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
        avg_diff = sum(diffs) / len(diffs)
        freq = next((label for label, lo, hi in _FREQ_RANGES if lo <= avg_diff <= hi), None)
        if freq is None:
            continue
        if len(diffs) > 1 and any(abs(d - avg_diff) / avg_diff > 0.4 for d in diffs):
            continue
        results.append({"name": str(key), "amount": round(float(median_amt), 2), "frequency": freq})

    results.sort(key=lambda x: x["amount"], reverse=True)
    return results[:20]


# ── Memory deduplication ──────────────────────────────────────────────────────

def _is_duplicate_memory(user_id: int, embedding: list[float], threshold: float = 0.92) -> bool:
    """Return True if a very similar memory already exists (cosine similarity >= threshold)."""
    try:
        with get_conn() as conn:
            conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
            row = conn.execute("""
                SELECT 1 - (embedding <=> %s::vector) AS similarity
                FROM conversation_memory
                WHERE user_id = %s AND embedding IS NOT NULL
                ORDER BY embedding <=> %s::vector
                LIMIT 1
            """, (embedding, user_id, embedding)).fetchone()
        return row is not None and float(row["similarity"]) >= threshold
    except Exception:
        return False  # fail open — store the memory rather than silently drop it


# ── Action extraction ─────────────────────────────────────────────────────────

_ACTION_SYSTEM = """You extract 2-4 specific, actionable next steps from a financial advisor's response.

Each action must be something the user can concretely DO — transfer money, set a savings target, cancel a subscription, etc. Ground amounts and dates in what was said in the response. Never generic ("save more money").

Output a JSON array. Each item:
  {"label": "short button text (≤ 40 chars)", "message": "the follow-up question the user would send to explore or execute this action (1 sentence, specific)"}

Examples of good actions:
  {"label": "Move $400 to HYSA this month", "message": "Help me figure out exactly when and how to move $400 into my HYSA to maximise this month's interest."}
  {"label": "Cancel unused subscriptions", "message": "Which of my recurring subscriptions should I consider cancelling, and what would that save me monthly?"}
  {"label": "Set a dining budget", "message": "Help me set a realistic dining budget based on my recent spending."}

Return only the JSON array, no commentary. If there are no clear actionable items, return []."""


def _extract_actions(user_message: str, advisor_response: str) -> list[dict]:
    """Ask Haiku to extract 2-4 actionable next steps from the advisor's response."""
    try:
        result = _anthropic.messages.create(
            model=EXTRACT_MODEL,
            max_tokens=400,
            system=_ACTION_SYSTEM,
            messages=[{
                "role": "user",
                "content": f"User asked: {user_message}\n\nAdvisor responded: {advisor_response}",
            }],
        )
        raw = result.content[0].text.strip()
        actions = json.loads(raw)
        if not isinstance(actions, list):
            return []
        # Validate shape — each item needs label + message strings
        return [
            a for a in actions[:4]
            if isinstance(a.get("label"), str) and isinstance(a.get("message"), str)
        ]
    except Exception as e:
        print(f"[advisor] action extraction failed: {e}")
        return []


# ── Query intent parsing ──────────────────────────────────────────────────────

_CATEGORIES = ["Food & Drink", "Transport", "Shopping", "Subscriptions",
               "Health", "Utilities", "Travel", "Payments", "Income / Interest", "Other"]

# Signals that the message is about general financial knowledge, not the user's transactions
_GENERAL_RE = re.compile(
    r'\b(how does|how do|what is|what are|what\'s|explain|tell me about|define|'
    r'describe|difference between|when should|should i|is it (better|worth)|'
    r'roth|401k|ira|hsa|index fund|etf|bond|stock|emergency fund|compound interest|'
    r'amortization|apr|apy|credit score|debt.to.income|dollar.cost)\b',
    re.IGNORECASE,
)

# Signals that the message references a specific time period — Haiku needed
_TIME_RE = re.compile(
    r'\b(last|this|past|previous|since|during|in|for|over the)\s+'
    r'(month|year|week|quarter|summer|winter|spring|fall|january|february|march|'
    r'april|may|june|july|august|september|october|november|december|\d+\s*(month|week|year|day)s?)\b'
    r'|\bytd\b|\byear.to.date\b|\bq[1-4]\b|\b\d{4}\b',
    re.IGNORECASE,
)

# Signals a specific category or merchant focus — Haiku needed
_SPECIFIC_RE = re.compile(
    r'\b(food|groceries|dining|eating out|restaurants?|coffee|transport|'
    r'uber|lyft|gas|shopping|amazon|netflix|spotify|subscriptions?|'
    r'health|gym|pharmacy|utilities|rent|travel|flights?|hotels?|'
    r'amazon|target|walmart|costco|doordash|grubhub|instacart)\b',
    re.IGNORECASE,
)

# Signals a trend/change-over-time question — Haiku needed
_TREND_RE = re.compile(
    r'\b(trend|over time|changed|increasing|decreasing|more than|less than|'
    r'compared to|vs\.?|versus|month.over.month|year.over.year|progress)\b',
    re.IGNORECASE,
)

_INTENT_SYSTEM = f"""You extract structured query parameters from financial questions.
Given a user message and today's date, output a JSON object with these fields:
- start_date: ISO date string (YYYY-MM-DD) for the start of the relevant period
- end_date: ISO date string (YYYY-MM-DD), usually today
- categories: list of category names from {_CATEGORIES} the user is asking about (empty = all)
- merchants: list of merchant/business names the user is asking about (empty = all)
- intent: one of "spending_summary" | "category_drill" | "merchant_drill" | "trend" | "general"

Use "trend" when the user asks how spending has changed over time.
Use "category_drill" or "merchant_drill" when focused on a specific category or merchant.
Use "spending_summary" for general spending overviews.

Time reference mappings (compute from today's date):
- "this month" → start of current calendar month to today
- "last month" → first to last day of the previous calendar month
- "last N months" / "past N months" → N*30 days back to today
- "this year" / "YTD" → Jan 1 of current year to today
- "last year" → Jan 1 to Dec 31 of previous year
- "last summer" → Jun 1 to Aug 31 of previous year

Respond with only the JSON object, no commentary."""


def _quick_classify(message: str) -> dict | None:
    """
    Regex-based fast path — returns an intent dict if the message is classifiable
    without an LLM call, or None if Haiku is needed.

    Skip Haiku when:
      - Clearly a general knowledge question with no personal spending references
      - Generic spending question with no time/category/merchant specificity
        (just use the 30-day default)

    Call Haiku when:
      - Message contains a specific time reference
      - Message names a specific category or merchant
      - Message asks about trends/changes over time
    """
    today = date.today()
    default_intent = {
        "start_date": (today - timedelta(days=30)).isoformat(),
        "end_date": today.isoformat(),
        "categories": [],
        "merchants": [],
        "intent": "spending_summary",
    }

    # If it's a knowledge question with no "my"/"I" personal reference, skip tx data entirely
    has_personal = bool(re.search(r"\b(my|i |i've|i'm|me)\b", message, re.IGNORECASE))
    if _GENERAL_RE.search(message) and not has_personal:
        return {**default_intent, "intent": "general"}

    # If any specificity signals are present, Haiku is needed
    if _TIME_RE.search(message) or _SPECIFIC_RE.search(message) or _TREND_RE.search(message):
        return None  # call Haiku

    # Generic personal spending question — 30-day default is fine
    return default_intent


def _parse_query_intent(user_message: str) -> dict:
    """Classify the message into structured query params. Uses regex fast path; falls
    back to a Haiku call only when the message has time/category/merchant specificity."""
    fast = _quick_classify(user_message)
    if fast is not None:
        return fast

    today = date.today().isoformat()
    default = {
        "start_date": (date.today() - timedelta(days=30)).isoformat(),
        "end_date": today,
        "categories": [],
        "merchants": [],
        "intent": "spending_summary",
    }
    try:
        result = _anthropic.messages.create(
            model=EXTRACT_MODEL,
            max_tokens=200,
            system=_INTENT_SYSTEM,
            messages=[{"role": "user", "content": f"Today: {today}\n\nUser message: {user_message}"}],
        )
        parsed = json.loads(result.content[0].text.strip())
        if not all(k in parsed for k in ("start_date", "end_date", "intent")):
            return default
        return parsed
    except Exception as e:
        print(f"[advisor] intent parsing failed: {e}")
        return default


def _fetch_relevant_transactions(intent: dict, df: pd.DataFrame) -> str:
    """Filter the transaction df by intent params and return a formatted context string."""
    if intent.get("intent") == "general" or df is None or df.empty:
        return ""

    try:
        start = pd.Timestamp(intent["start_date"])
        end = pd.Timestamp(intent["end_date"])
        filtered = df[(df["date"] >= start) & (df["date"] <= end)].copy()

        categories = [c.lower() for c in intent.get("categories", [])]
        if categories:
            filtered = filtered[
                filtered["category"].str.lower().apply(
                    lambda c: any(cat in c or c in cat for cat in categories)
                )
            ]

        merchants = [m.lower() for m in intent.get("merchants", [])]
        if merchants:
            filtered = filtered[
                filtered["merchant_normalized"].str.lower().apply(
                    lambda m: any(mer in m or m in mer for mer in merchants)
                )
            ]

        spending = get_spending(filtered)
        period = f"{intent['start_date']} to {intent['end_date']}"

        if spending.empty:
            return f"## Relevant Transaction Data\nPeriod: {period}\nNo spending found matching this query.\n"

        intent_type = intent.get("intent", "spending_summary")
        total = spending["amount"].sum()
        lines = [
            f"## Relevant Transaction Data",
            f"Period: {period}",
            f"Total: ${total:,.2f} across {len(spending)} transactions",
        ]

        if intent_type == "trend":
            monthly = spending_by_month(filtered)
            if not monthly.empty:
                lines.append("Monthly breakdown:")
                for _, row in monthly.iterrows():
                    lines.append(f"  {row['month']}: ${row['total']:,.2f} ({int(row['count'])} txns)")

        elif intent_type == "spending_summary":
            by_cat = spending_by_category(filtered)
            if not by_cat.empty:
                lines.append("By category:")
                for _, row in by_cat.head(10).iterrows():
                    lines.append(f"  {row['category']}: ${row['total']:,.2f} ({row['pct']:.0f}%)")

        elif intent_type in ("category_drill", "merchant_drill"):
            top = spending.nlargest(20, "amount")[["date", "merchant_normalized", "category", "amount"]]
            lines.append("Top transactions:")
            for _, row in top.iterrows():
                lines.append(
                    f"  {row['date'].strftime('%Y-%m-%d')} — {row['merchant_normalized']}"
                    f" — {row['category']} — ${row['amount']:,.2f}"
                )

        return "\n".join(lines) + "\n"

    except Exception as e:
        print(f"[advisor] transaction fetch failed: {e}")
        return ""


# ── Context assembly ──────────────────────────────────────────────────────────

def _build_system_prompt(user_id: int, user_message: str) -> str:
    today = date.today().isoformat()

    # Load transactions once — reused for snapshot fallback and smart fetch
    try:
        df = load_data(user_id)
    except Exception:
        df = pd.DataFrame()

    # Parse what the user is actually asking about
    intent = _parse_query_intent(user_message)

    # 1. User identity
    profile = get_user_profile(user_id) or {}
    fin_profile = get_user_financial_profile(user_id) or {}
    name = f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip() or "the user"

    identity_section = f"## User\nName: {name}\nToday: {today}"
    if fin_profile.get("life_stage"):
        identity_section += f"\nLife stage: {fin_profile['life_stage']}"
    if fin_profile.get("risk_tolerance"):
        identity_section += f"\nRisk tolerance: {fin_profile['risk_tolerance']}"
    if fin_profile.get("income_estimate"):
        identity_section += f"\nEstimated annual income: ${fin_profile['income_estimate']:,.0f}"
    if fin_profile.get("communication_style"):
        identity_section += f"\nCommunication style: {fin_profile['communication_style']}"

    # 2. Financial snapshot — derive from transactions if stale/missing
    snapshots = list_financial_snapshots(user_id, limit=3)
    snapshot_stale = (
        not snapshots or
        (date.today() - date.fromisoformat(str(snapshots[0]["snapshot_date"]))).days > 30
    )
    if snapshot_stale and not df.empty:
        try:
            # Derive from last 30 days of transactions
            cutoff = pd.Timestamp(date.today() - timedelta(days=30))
            recent = df[df["date"] >= cutoff]
            clean = recent[
                (~recent.get("is_transfer", pd.Series(False, index=recent.index)).fillna(False)) &
                (~recent.get("is_duplicate", pd.Series(False, index=recent.index)).fillna(False))
            ] if not recent.empty else recent

            credit = clean[clean["type"] == "credit"]["amount"].abs().sum() if "type" in clean.columns else 0
            debit  = clean[clean["type"] == "debit"]["amount"].sum()         if "type" in clean.columns else 0
            income_monthly  = round(float(credit), 2)
            expenses_monthly = round(float(debit), 2)
            savings_rate = round((income_monthly - expenses_monthly) / income_monthly * 100, 1) if income_monthly > 0 else 0.0

            # Also pull from user profile for income estimate if transactions look incomplete
            if income_monthly < 100 and fin_profile.get("income_estimate"):
                income_monthly = round(fin_profile["income_estimate"] / 12, 2)
                savings_rate = None  # can't compute accurately

            snap_id = create_financial_snapshot(
                user_id=user_id,
                income_estimate=income_monthly,
                total_expenses=expenses_monthly,
                savings_rate_pct=savings_rate,
            )
            snapshots = list_financial_snapshots(user_id, limit=3)
            print(f"[advisor] derived snapshot for user {user_id}: income=${income_monthly}, expenses=${expenses_monthly}, savings_rate={savings_rate}%")
        except Exception as e:
            print(f"[advisor] snapshot derivation failed: {e}")

    snapshot_section = "## Financial Snapshot\n"
    if snapshots:
        s = snapshots[0]
        snapshot_section += f"As of {s['snapshot_date']} (30-day period):\n"
        if s.get("income_estimate") is not None:
            snapshot_section += f"- Monthly income (from transactions): ${s['income_estimate']:,.0f}\n"
        if s.get("total_expenses") is not None:
            snapshot_section += f"- Monthly expenses: ${s['total_expenses']:,.0f}\n"
        if s.get("savings_rate_pct") is not None:
            snapshot_section += f"- Savings rate: {s['savings_rate_pct']:.1f}%\n"
        if s.get("net_worth") is not None:
            snapshot_section += f"- Net worth: ${s['net_worth']:,.0f}\n"
        if len(snapshots) > 1:
            prev = snapshots[1]
            if s.get("savings_rate_pct") and prev.get("savings_rate_pct"):
                delta = s["savings_rate_pct"] - prev["savings_rate_pct"]
                direction = "up" if delta > 0 else "down"
                snapshot_section += f"- Savings rate trend: {direction} {abs(delta):.1f}pp vs prior period\n"

        # Spending velocity — current-month spend prorated to end of month
        if not df.empty:
            try:
                _today = date.today()
                _month_start = pd.Timestamp(_today.replace(day=1))
                # last day of current month
                if _today.month == 12:
                    _next_month = _today.replace(year=_today.year + 1, month=1, day=1)
                else:
                    _next_month = _today.replace(month=_today.month + 1, day=1)
                _days_in_month = (_next_month - _today.replace(day=1)).days
                _days_elapsed = _today.day

                _mtd = df[
                    (df["date"] >= _month_start) &
                    (~df.get("is_transfer", pd.Series(False, index=df.index)).fillna(False)) &
                    (~df.get("is_duplicate", pd.Series(False, index=df.index)).fillna(False)) &
                    (df["type"] == "debit")
                ]["amount"].sum()

                if _mtd > 0 and _days_elapsed > 0:
                    _pace = (_mtd / _days_elapsed) * _days_in_month
                    snapshot_section += (
                        f"- This month so far: ${_mtd:,.0f} spent ({_days_elapsed}/{_days_in_month} days) "
                        f"— on pace for ${_pace:,.0f} by month-end\n"
                    )
            except Exception:
                pass
    else:
        snapshot_section += "No snapshot data available yet.\n"

    # 3. Relevant transaction data — pulled based on what the user is actually asking
    tx_section = _fetch_relevant_transactions(intent, df)

    # 4. Active goals
    goals = list_goals(user_id, status="active")
    goals_section = "## Active Goals\n"
    if goals:
        for g in goals:
            line = f"- {g['title']}"
            if g.get("target_amount"):
                pct = (g["current_amount"] / g["target_amount"] * 100) if g["target_amount"] else 0
                line += f" — ${g['current_amount']:,.0f} / ${g['target_amount']:,.0f} ({pct:.0f}%)"
            if g.get("deadline"):
                line += f", deadline {g['deadline']}"
            goals_section += line + "\n"
    else:
        goals_section += "No active goals set yet.\n"

    # 5. Budgets (with current-month spend)
    budgets = _fetch_budgets_with_spend(user_id, df)
    budgets_section = "## Budgets\n"
    if budgets:
        for b in budgets:
            pct = (b["spent"] / b["amount"] * 100) if b["amount"] else 0
            status = "over budget" if pct > 100 else ("on pace" if pct <= 75 else "close to limit")
            budgets_section += (
                f"- {b['category']}: ${b['spent']:,.0f} / ${b['amount']:,.0f} "
                f"({pct:.0f}% — {status})\n"
            )
    else:
        budgets_section += "No budgets set.\n"

    # 6. Recurring transactions
    recurring = _detect_recurring_simple(df)
    recurring_section = "## Recurring Transactions\n"
    if recurring:
        for r in recurring:
            recurring_section += f"- {r['name']}: ${r['amount']:,.2f} {r['frequency']}\n"
    else:
        recurring_section += "None detected.\n"

    # 7. Relevant memories — pinned high-importance + semantic search
    memories_section = "## Relevant Context From Past Conversations\n"
    try:
        query_embedding = get_query_embedding(user_message)

        # Always include high-importance memories (goals, events, major context)
        with get_conn() as conn:
            conn.execute("SET LOCAL app.current_user_id = %s", (str(user_id),))
            pinned_rows = conn.execute("""
                SELECT id, content, memory_type, importance
                FROM conversation_memory
                WHERE user_id = %s AND importance >= 4
                ORDER BY importance DESC, created_at DESC
                LIMIT 6
            """, (user_id,)).fetchall()
        pinned_ids = {r["id"] for r in pinned_rows}

        # Semantic search — fill remaining slots, excluding already-pinned
        semantic = retrieve_relevant_memories(user_id, query_embedding, limit=10)
        semantic_extra = [m for m in semantic if m["id"] not in pinned_ids][:5]

        all_memories = [dict(r) for r in pinned_rows] + semantic_extra
        if all_memories:
            for m in all_memories:
                tag = f"{m['memory_type']}, importance {m['importance']}" if m.get("importance", 3) >= 4 else m["memory_type"]
                memories_section += f"- [{tag}] {m['content']}\n"
        else:
            memories_section += "No prior context available.\n"
    except Exception:
        memories_section += "Memory retrieval unavailable.\n"

    # 8. Recent advice history (last 3 to avoid repetition)
    history_section = "## Recent Advice Given\n"
    recent_advice = list_advice(user_id, limit=3)
    if recent_advice:
        for a in recent_advice:
            reaction = a.get("user_reaction", "unknown")
            summary = a.get("prompt_summary") or "general advice"
            history_section += f"- {a['created_at'].strftime('%Y-%m-%d')}: {summary} (user reaction: {reaction})\n"
    else:
        history_section += "No prior advice on record.\n"

    # 9. Tone guidance — from CLAUDE.md "How the Advisor Should Talk"
    style = fin_profile.get("communication_style", "direct")
    style_instructions = {
        "direct":      "Be concise and direct. Lead with the answer, then the reasoning. Skip preamble.",
        "detailed":    "Be thorough. Walk through the reasoning step by step. Include the math.",
        "encouraging": "Be warm and encouraging. Acknowledge effort and progress before diving into numbers.",
        "analytical":  "Lead with data and metrics. Show the numbers first, then the interpretation.",
    }.get(style, "Be concise and direct. Lead with the answer, then the reasoning.")

    tone_section = f"""## How to Respond
Be direct, specific, and actionable. Give real answers with real numbers — no generic platitudes.

If someone asks "should I pay off my credit card or invest?", give them a real answer using their actual numbers, not a hedge.

Do NOT say "consult a professional" for straightforward financial questions. Only suggest a CFP or CPA when the question genuinely requires licensed advice (e.g. complex tax situations, estate planning, insurance underwriting).

Be honest when you don't know something. A confident wrong answer is worse than "I'm not sure about that."

Communication style for this user: {style_instructions}"""

    # 10. Guardrails (narrow — only what truly requires a license)
    guardrails_section = """## Guardrails
You are a financial coach and educator, not a registered investment advisor.

Do NOT:
- Recommend specific securities, ETFs, funds, or crypto tickers to buy or sell
- Claim guaranteed returns on any investment
- Provide specific tax filing advice (refer to a CPA for that)

You CAN and SHOULD:
- Explain how any financial product or strategy works, including tax-advantaged accounts
- Show the user their numbers and what those numbers mean
- Discuss general principles backed by historical data
- Say "historically X has been better for most people in your situation" — that is coaching, not advising"""

    sections = [
        "You are a personal financial advisor AI with full context about this user. Use everything below to give specific, grounded advice — not generic tips.",
        identity_section,
        snapshot_section,
    ]
    if tx_section:
        sections.append(tx_section)
    sections += [
        goals_section,
        budgets_section,
        recurring_section,
        memories_section,
        history_section,
        tone_section,
        guardrails_section,
    ]
    return "\n\n".join(sections)


# ── Chat endpoint ─────────────────────────────────────────────────────────────

@router.post("/chat", response_model=ChatResponse)
@limiter.limit("30/minute")
def chat(request: Request, body: ChatRequest, current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]

    system_prompt = _build_system_prompt(user_id, body.message)

    # Sanitize history: only allow valid roles and string content, cap at 20 items
    safe_history = [
        {"role": m["role"], "content": m["content"]}
        for m in body.history[-20:]
        if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)
    ]

    t0 = time.monotonic()
    try:
        result = _anthropic.messages.create(
            model=ADVISOR_MODEL,
            max_tokens=1024,
            system=system_prompt,
            messages=[*safe_history, {"role": "user", "content": body.message}],
        )
    except anthropic.APIError as e:
        raise HTTPException(status_code=502, detail=f"Advisor unavailable: {e}")
    latency_ms = int((time.monotonic() - t0) * 1000)

    raw_response = result.content[0].text

    # Compliance check — may rewrite red-flag responses
    response_text, flags = _compliance_check(raw_response)

    # Persist the advice with full LLM call metadata
    prompt_summary = body.message[:120] + ("…" if len(body.message) > 120 else "")
    advice_id = store_advice(
        user_id=user_id,
        response_text=response_text,
        prompt_summary=prompt_summary,
        user_message=body.message,
        category=None,
        compliance_flags=flags,
        prompt_tokens=result.usage.input_tokens,
        completion_tokens=result.usage.output_tokens,
        latency_ms=latency_ms,
    )
    print(f"[advisor] chat — {result.usage.input_tokens}pt / {result.usage.output_tokens}ct / {latency_ms}ms")

    # Extract memories and actions in parallel threads (best-effort)
    threading.Thread(
        target=_extract_and_store_memories,
        args=(user_id, body.message, response_text),
        daemon=True,
    ).start()
    actions = _extract_actions(body.message, response_text)

    return ChatResponse(
        response=response_text,
        advice_id=advice_id,
        compliance_flags=flags,
        actions=actions,
    )


# ── Streaming chat endpoint ───────────────────────────────────────────────────

@router.post("/chat/stream")
@limiter.limit("30/minute")
def chat_stream(request: Request, body: ChatRequest, current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]

    system_prompt = _build_system_prompt(user_id, body.message)
    safe_history = [
        {"role": m["role"], "content": m["content"]}
        for m in body.history[-20:]
        if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)
    ]

    def generate():
        accumulated: list[str] = []
        t0 = time.monotonic()

        try:
            with _anthropic.messages.stream(
                model=ADVISOR_MODEL,
                max_tokens=1024,
                system=system_prompt,
                messages=[*safe_history, {"role": "user", "content": body.message}],
            ) as stream:
                for text in stream.text_stream:
                    accumulated.append(text)
                    yield f"data: {json.dumps({'type': 'delta', 'text': text})}\n\n"
                final_message = stream.get_final_message()
                usage = final_message.usage
        except anthropic.APIError as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
            return

        latency_ms = int((time.monotonic() - t0) * 1000)
        raw_response = "".join(accumulated)

        # Compliance check — may rewrite or append to the response
        response_text, flags = _compliance_check(raw_response)

        if response_text != raw_response:
            # Red flag: rewritten response — replace what the client displayed
            yield f"data: {json.dumps({'type': 'correction', 'text': response_text})}\n\n"
        elif len(response_text) > len(raw_response):
            # Yellow flag: disclaimer appended — stream it as a final delta
            yield f"data: {json.dumps({'type': 'delta', 'text': response_text[len(raw_response):]})}\n\n"

        prompt_summary = body.message[:120] + ("…" if len(body.message) > 120 else "")
        advice_id = store_advice(
            user_id=user_id,
            response_text=response_text,
            prompt_summary=prompt_summary,
            user_message=body.message,
            category=None,
            compliance_flags=flags,
            prompt_tokens=usage.input_tokens,
            completion_tokens=usage.output_tokens,
            latency_ms=latency_ms,
        )
        print(f"[advisor] stream — {usage.input_tokens}pt / {usage.output_tokens}ct / {latency_ms}ms")

        threading.Thread(
            target=_extract_and_store_memories,
            args=(user_id, body.message, response_text),
            daemon=True,
        ).start()

        actions = _extract_actions(body.message, response_text)
        yield f"data: {json.dumps({'type': 'done', 'advice_id': advice_id, 'flags': flags, 'actions': actions})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Onboarding endpoint ───────────────────────────────────────────────────────

@router.post("/onboard", response_model=OnboardResponse)
@limiter.limit("30/minute")
def onboard(request: Request, body: OnboardRequest, current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]

    safe_history = [
        {"role": m["role"], "content": m["content"]}
        for m in body.history[-20:]
        if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)
    ]

    try:
        result = _anthropic.messages.create(
            model=ADVISOR_MODEL,
            max_tokens=1024,
            system=_ONBOARD_SYSTEM,
            tools=[_SUGGEST_OPTIONS_TOOL, _ONBOARD_TOOL],
            messages=[*safe_history, {"role": "user", "content": body.message}],
        )
    except anthropic.APIError as e:
        raise HTTPException(status_code=502, detail=f"Advisor unavailable: {e}")

    tool_blocks = [b for b in result.content if b.type == "tool_use"]
    complete_block = next((b for b in tool_blocks if b.name == "complete_onboarding"), None)
    options_block = next((b for b in tool_blocks if b.name == "suggest_options"), None)

    # If Claude called complete_onboarding, save the profile and finish
    if complete_block:
        data = complete_block.input
        upsert_user_financial_profile(
            user_id,
            life_stage=data.get("life_stage"),
            risk_tolerance=data.get("risk_tolerance"),
            income_estimate=data.get("income_estimate"),
            communication_style=data.get("communication_style", "direct"),
        )
        for g in data.get("goals", []):
            try:
                create_goal(
                    user_id=user_id,
                    title=g["title"],
                    type=g.get("type", "other"),
                    target_amount=g.get("target_amount"),
                    deadline=g.get("deadline"),
                )
            except Exception:
                pass
        try:
            summary = (
                f"Onboarding profile: {data.get('life_stage')} life stage, "
                f"income ~${data.get('income_estimate', 0):,.0f}/yr, "
                f"{data.get('risk_tolerance')} risk tolerance, "
                f"communication style: {data.get('communication_style', 'direct')}"
            )
            embedding = get_embedding(summary)
            store_memory(user_id=user_id, content=summary, memory_type="context",
                         importance=5, source="onboarding", embedding=embedding)
        except Exception:
            pass
        closing = data.get("closing_message", "You're all set — let's get to work!")
        return OnboardResponse(response=closing, completed=True)

    # Still gathering info — return text + any options Claude suggested
    text_block = next((b for b in result.content if b.type == "text"), None)
    response_text = text_block.text if text_block else "Tell me a bit more about your situation."
    options = options_block.input.get("options", []) if options_block else []
    return OnboardResponse(response=response_text, completed=False, options=options)


# ── Profile endpoints ─────────────────────────────────────────────────────────

@router.get("/profile")
def get_profile(current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    profile = get_user_profile(user_id) or {}
    fin = get_user_financial_profile(user_id) or {}
    return {
        "first_name": profile.get("first_name"),
        "last_name": profile.get("last_name"),
        "life_stage": fin.get("life_stage"),
        "risk_tolerance": fin.get("risk_tolerance"),
        "income_estimate": fin.get("income_estimate"),
        "communication_style": fin.get("communication_style"),
        "has_profile": bool(fin),
    }


@router.put("/profile")
def update_profile_endpoint(body: ProfileUpdate, current_user: dict = Depends(get_current_user)):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if updates:
        upsert_user_financial_profile(current_user["id"], **updates)
    return {"ok": True}


# ── Tracker endpoint ──────────────────────────────────────────────────────────

@router.get("/tracker")
def tracker(current_user: dict = Depends(get_current_user)):
    import calendar as _cal

    user_id = current_user["id"]
    today = date.today()
    month_start = pd.Timestamp(today.replace(day=1))
    days_in_month = _cal.monthrange(today.year, today.month)[1]

    try:
        df = load_data(user_id)
    except Exception:
        df = pd.DataFrame()

    # ── Goals with pace ───────────────────────────────────────────────────────
    goals = list_goals(user_id)
    for g in goals:
        for f in ("created_at", "updated_at"):
            if g.get(f):
                g[f] = g[f].isoformat()
        if g.get("deadline"):
            g["deadline"] = str(g["deadline"])

        target = float(g["target_amount"]) if g.get("target_amount") is not None else None
        current = float(g.get("current_amount") or 0)
        dl = g.get("deadline")

        g["days_left"] = None
        g["monthly_needed"] = None
        g["pct"] = round(current / target * 100, 1) if target else None

        if target and dl:
            days_left = (date.fromisoformat(str(dl)) - today).days
            g["days_left"] = days_left
            remaining = target - current
            months_left = days_left / 30.44
            if days_left > 0 and remaining > 0 and months_left > 0:
                g["monthly_needed"] = round(remaining / months_left, 2)

    # ── MTD filtered df ───────────────────────────────────────────────────────
    mtd_df = pd.DataFrame()
    if not df.empty:
        mtd_df = df[
            (df["date"] >= month_start) &
            (~df.get("is_transfer", pd.Series(False, index=df.index)).fillna(False)) &
            (~df.get("is_duplicate", pd.Series(False, index=df.index)).fillna(False)) &
            (df["type"] == "debit")
        ].copy()

    # ── Budgets with top transactions + pace ──────────────────────────────────
    budgets = _fetch_budgets_with_spend(user_id, df)
    for b in budgets:
        cat = b["category"]
        b["pace"] = round((b["spent"] / today.day) * days_in_month, 2) if today.day and not mtd_df.empty else 0

        if not mtd_df.empty and "category" in mtd_df.columns:
            cat_txns = (
                mtd_df[mtd_df["category"].str.lower() == cat.lower()]
                .nlargest(5, "amount")[["date", "merchant_normalized", "name", "amount"]]
            )
            b["top_transactions"] = [
                {
                    "date": row["date"].strftime("%Y-%m-%d"),
                    "name": (row.get("merchant_normalized") or row.get("name") or "").strip(),
                    "amount": round(float(row["amount"]), 2),
                }
                for _, row in cat_txns.iterrows()
            ]

            # Month-over-month: last 3 months spend for this category
            monthly = []
            for offset in range(2, -1, -1):
                if today.month - offset < 1:
                    yr, mo = today.year - 1, today.month - offset + 12
                else:
                    yr, mo = today.year, today.month - offset
                mo_start = pd.Timestamp(date(yr, mo, 1))
                mo_end = pd.Timestamp(date(yr, mo, _cal.monthrange(yr, mo)[1]))
                mo_df = df[
                    (df["date"] >= mo_start) & (df["date"] <= mo_end) &
                    (df["category"].str.lower() == cat.lower()) &
                    (~df.get("is_transfer", pd.Series(False, index=df.index)).fillna(False)) &
                    (~df.get("is_duplicate", pd.Series(False, index=df.index)).fillna(False)) &
                    (df["type"] == "debit")
                ] if not df.empty else pd.DataFrame()
                monthly.append({
                    "month": f"{yr}-{mo:02d}",
                    "total": round(float(mo_df["amount"].sum()), 2) if not mo_df.empty else 0.0,
                })
            b["monthly_trend"] = monthly
        else:
            b["top_transactions"] = []
            b["monthly_trend"] = []

    # ── Recurring ─────────────────────────────────────────────────────────────
    # Use the workspace detector so user-marked recurring rules are honored.
    from backend.routers.workspace import _detect_recurring, _fetch_user_rules
    recurring = _detect_recurring(df, _fetch_user_rules(user_id))

    # ── Snapshots (last 6 months for health trend) ────────────────────────────
    snapshots = list_financial_snapshots(user_id, limit=6)
    for s in snapshots:
        if s.get("snapshot_date"):
            s["snapshot_date"] = str(s["snapshot_date"])
        if s.get("created_at"):
            s["created_at"] = s["created_at"].isoformat()

    # ── MTD summary ───────────────────────────────────────────────────────────
    mtd_total = round(float(mtd_df["amount"].sum()), 2) if not mtd_df.empty else 0.0
    mtd_pace = round((mtd_total / today.day) * days_in_month, 2) if today.day else 0.0
    total_budget = sum(b["amount"] for b in budgets)
    total_recurring_monthly = sum(
        r["amount"] * {"weekly": 4.33, "biweekly": 2.17, "monthly": 1,
                       "quarterly": 1 / 3, "annual": 1 / 12}.get(r["frequency"], 1)
        for r in recurring
    )

    return {
        "goals": goals,
        "budgets": budgets,
        "recurring": recurring,
        "snapshots": snapshots,
        "summary": {
            "mtd_spent": mtd_total,
            "mtd_pace": mtd_pace,
            "days_elapsed": today.day,
            "days_in_month": days_in_month,
            "total_budget": round(total_budget, 2),
            "total_recurring_monthly": round(total_recurring_monthly, 2),
        },
    }


# ── Goals CRUD ────────────────────────────────────────────────────────────────

@router.get("/goals")
def list_user_goals(status: str = None, current_user: dict = Depends(get_current_user)):
    goals = list_goals(current_user["id"], status=status)
    for g in goals:
        if g.get("created_at"):
            g["created_at"] = g["created_at"].isoformat()
        if g.get("updated_at"):
            g["updated_at"] = g["updated_at"].isoformat()
    return goals


@router.post("/goals", status_code=201)
def create_user_goal(body: GoalCreate, current_user: dict = Depends(get_current_user)):
    goal_id = create_goal(
        user_id=current_user["id"],
        title=body.title,
        type=body.type,
        target_amount=body.target_amount,
        current_amount=body.current_amount,
        deadline=body.deadline,
        priority=body.priority,
        notes=body.notes,
    )
    return {"id": goal_id}


@router.put("/goals/{goal_id}")
def update_user_goal(goal_id: int, body: GoalUpdate, current_user: dict = Depends(get_current_user)):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    existing = get_goal(current_user["id"], goal_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Goal not found")
    update_goal(current_user["id"], goal_id, **updates)
    return {"ok": True}


@router.delete("/goals/{goal_id}", status_code=204)
def delete_user_goal(goal_id: int, current_user: dict = Depends(get_current_user)):
    existing = get_goal(current_user["id"], goal_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Goal not found")
    delete_goal(current_user["id"], goal_id)


# ── Advice history ────────────────────────────────────────────────────────────

@router.get("/history")
def advice_history(limit: int = 50, current_user: dict = Depends(get_current_user)):
    history = list_advice(current_user["id"], limit=limit)
    for h in history:
        if h.get("created_at"):
            h["created_at"] = h["created_at"].isoformat()
    return history


@router.patch("/history/{advice_id}/reaction")
def react_to_advice(advice_id: int, body: ReactionUpdate, current_user: dict = Depends(get_current_user)):
    update_advice_reaction(current_user["id"], advice_id, body.reaction, body.outcome_notes)
    return {"ok": True}
