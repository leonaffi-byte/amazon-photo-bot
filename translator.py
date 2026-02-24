"""
translator.py — Language detection, translation and query refinement.

Detects Hebrew (RTL) and Russian (Cyrillic) text and translates it to English.
Also refines free-text product descriptions into tight Amazon search queries.

The cheapest configured LLM is used (Gemini > GPT-4o-mini > Claude Haiku > Groq).
No extra dependencies required — all SDKs are already in requirements.txt.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# ── Character-range regexes ────────────────────────────────────────────────────
_HEBREW_RE   = re.compile(r"[\u0590-\u05FF\uFB1D-\uFB4F]")
_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")


def detect_language(text: str) -> str:
    """Return 'he', 'ru', or 'en' based on character ranges."""
    if _HEBREW_RE.search(text):
        return "he"
    if _CYRILLIC_RE.search(text):
        return "ru"
    return "en"


# ── Prompts ────────────────────────────────────────────────────────────────────

_REFINE_PROMPT = (
    "You are a product search assistant. Convert the user's product description "
    "into a precise Amazon search query (English only, ≤80 characters). "
    "Output ONLY the search query — no quotes, no explanation, no punctuation at end."
)

_TRANSLATE_PROMPT = (
    "Translate the following {lang} text to English, then produce a concise Amazon "
    "product search query for it.\n"
    "Reply with exactly two lines:\n"
    "Line 1: English translation\n"
    "Line 2: Amazon search query (≤80 chars, most specific terms first)\n\n"
    "Text: {text}"
)


async def translate_and_refine(text: str) -> tuple[str, str]:
    """
    Detect language, translate to English if needed, then refine into an
    Amazon search query.

    Returns:
        (english_text, amazon_query)
    """
    lang = detect_language(text)

    if lang == "en":
        refined = await _call_llm(_REFINE_PROMPT + "\n\nProduct description: " + text)
        refined = (refined or text).strip()[:200]
        return text, refined

    lang_name = {"he": "Hebrew", "ru": "Russian"}.get(lang, "unknown")
    prompt = _TRANSLATE_PROMPT.format(lang=lang_name, text=text)
    raw = await _call_llm(prompt)

    if not raw:
        return text, text

    lines = [ln.strip() for ln in raw.strip().splitlines() if ln.strip()]
    if len(lines) >= 2:
        return lines[0][:300], lines[1][:200]
    if len(lines) == 1:
        return lines[0][:300], lines[0][:200]
    return text, text


# ── LLM caller — tries cheapest provider first ────────────────────────────────

async def _call_llm(prompt: str) -> Optional[str]:
    """
    Try each configured provider in cheapest-first order.
    Returns the raw text response or None if all fail.
    """
    import key_store

    # ── Gemini Flash (cheapest) ───────────────────────────────────────────────
    google_key = await key_store.get("google_api_key")
    if google_key:
        try:
            from google import genai
            client = genai.Client(api_key=google_key, http_options={"api_version": "v1"})
            resp = await client.aio.models.generate_content(
                model="gemini-2.0-flash-001",
                contents=prompt,
            )
            return resp.text
        except Exception as exc:
            logger.warning("Gemini LLM call failed: %s", exc)

    # ── OpenAI GPT-4o-mini ────────────────────────────────────────────────────
    openai_key = await key_store.get("openai_api_key")
    if openai_key:
        try:
            import openai
            client = openai.AsyncOpenAI(api_key=openai_key)
            resp = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
                temperature=0,
            )
            return resp.choices[0].message.content
        except Exception as exc:
            logger.warning("OpenAI LLM call failed: %s", exc)

    # ── Claude Haiku ──────────────────────────────────────────────────────────
    anthropic_key = await key_store.get("anthropic_api_key")
    if anthropic_key:
        try:
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=anthropic_key)
            msg = await client.messages.create(
                model="claude-3-haiku-20240307",
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text
        except Exception as exc:
            logger.warning("Anthropic LLM call failed: %s", exc)

    # ── Groq (Llama 3.3 — fast & free) ───────────────────────────────────────
    groq_key = await key_store.get("groq_api_key")
    if groq_key:
        try:
            import openai
            client = openai.AsyncOpenAI(
                api_key=groq_key,
                base_url="https://api.groq.com/openai/v1",
            )
            resp = await client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
                temperature=0,
            )
            return resp.choices[0].message.content
        except Exception as exc:
            logger.warning("Groq LLM call failed: %s", exc)

    logger.error("translate_and_refine: no LLM available for text call")
    return None
