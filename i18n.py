"""
Internationalization (i18n) module for Amazon Photo Bot.

Provides translation loading, lookup with fallback, and language listing.
"""

import json
import os
import logging
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

SUPPORTED_LANGS = ("en", "he", "ru")
DEFAULT_LANG = "en"

# Language code -> native name mapping
_LANG_NAMES = {
    "en": "English",
    "he": "עברית",
    "ru": "Русский",
}

# Global locale storage: { "en": { "key": "value", ... }, ... }
_locales: Dict[str, Dict[str, str]] = {}


def load_locales(locale_dir: str | None = None) -> None:
    """Load all locale JSON files from the locale/ directory.

    Args:
        locale_dir: Path to directory containing locale JSON files.
                    Defaults to locale/ next to this file.
    """
    global _locales

    if locale_dir is None:
        locale_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locale")

    _locales.clear()

    for lang in SUPPORTED_LANGS:
        filepath = os.path.join(locale_dir, f"{lang}.json")
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                _locales[lang] = json.load(f)
            logger.info("Loaded locale: %s (%d keys)", lang, len(_locales[lang]))
        except FileNotFoundError:
            logger.warning("Locale file not found: %s", filepath)
            _locales[lang] = {}
        except json.JSONDecodeError as e:
            logger.error("Invalid JSON in locale file %s: %s", filepath, e)
            _locales[lang] = {}


def t(key: str, lang: str = DEFAULT_LANG, **kwargs) -> str:
    """Get a translated string by key and language.

    Falls back to English if the key is missing in the target language.
    Falls back to the raw key if missing in English too.
    Supports Python format-style substitution via kwargs.

    Args:
        key: The translation key (e.g. "welcome", "product_counter").
        lang: Language code (e.g. "en", "he", "ru").
        **kwargs: Format substitution variables (e.g. current=1, total=5).

    Returns:
        The translated, formatted string.
    """
    # Auto-load if not loaded yet
    if not _locales:
        load_locales()

    # Look up in target language, fall back to English, fall back to key
    text = None
    if lang in _locales:
        text = _locales[lang].get(key)
    if text is None and lang != DEFAULT_LANG:
        text = _locales.get(DEFAULT_LANG, {}).get(key)
    if text is None:
        logger.warning("Missing translation key: %s (lang=%s)", key, lang)
        return key

    # Apply format substitution
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError) as e:
            logger.warning("Format error for key %s (lang=%s): %s", key, lang, e)

    return text


def available_languages() -> List[Tuple[str, str]]:
    """Return list of (code, native_name) tuples for the language picker.

    Returns:
        List of tuples like [("en", "English"), ("he", "עברית"), ("ru", "Русский")].
    """
    return [(code, _LANG_NAMES.get(code, code)) for code in SUPPORTED_LANGS]
