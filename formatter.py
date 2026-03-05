"""
formatter.py -- Platform-aware message formatter for multi-platform bot.

Replaces style.py with support for Telegram, WhatsApp, Discord,
Instagram, Messenger, Viber, and LINE.

Each platform has its own bold/italic/link syntax, escaping rules,
and caption length limits.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from i18n import t

if TYPE_CHECKING:
    from providers.base import ProviderResult
    from search_backends.base import AmazonItem


# -- Caption limits per platform -----------------------------------------------

_CAPTION_LIMITS = {
    "telegram":  1020,
    "whatsapp":  1020,
    "instagram": 1000,
    "messenger": 636,
    "viber":     508,
    "discord":   4090,
    "line":      1996,
}

# Telegram MarkdownV2 special characters that must be escaped
_TG_SPECIAL = r"\_*[]()~`>#+-=|{}.!"

# Confidence -> color emoji
_CONF_EMOJI = {"high": "\U0001f7e2", "medium": "\U0001f7e1", "low": "\U0001f534"}


class Formatter:
    """Platform-aware message formatter.

    Args:
        platform: One of telegram, whatsapp, discord, instagram,
                  messenger, viber, line.
        lang: Language code for i18n (default "en").
    """

    def __init__(self, platform: str, lang: str = "en") -> None:
        self.platform = platform.lower()
        self.lang = lang

    # -- Properties ------------------------------------------------------------

    @property
    def max_caption_length(self) -> int:
        return _CAPTION_LIMITS.get(self.platform, 1020)

    # -- Internal helpers ------------------------------------------------------

    def _bold(self, text: str) -> str:
        """Wrap text in platform-specific bold markers."""
        if self.platform == "telegram":
            return f"*{text}*"
        if self.platform == "whatsapp":
            return f"*{text}*"
        if self.platform == "discord":
            return f"**{text}**"
        # plain-text platforms (instagram, messenger, viber, line)
        return text

    def _italic(self, text: str) -> str:
        """Wrap text in platform-specific italic markers."""
        if self.platform == "telegram":
            return f"_{text}_"
        if self.platform == "whatsapp":
            return f"_{text}_"
        if self.platform == "discord":
            return f"*{text}*"
        return text

    def _esc(self, text: str) -> str:
        """Escape platform-specific special characters."""
        if self.platform == "telegram":
            for ch in _TG_SPECIAL:
                text = text.replace(ch, f"\\{ch}")
            return text
        # WhatsApp / Discord / plain -- minimal or no escaping needed
        return text

    def _link(self, label: str, url: str) -> str:
        """Create an inline link or fall back to raw URL."""
        if self.platform == "telegram":
            return f"[{self._esc(label)}]({url})"
        if self.platform == "discord":
            return f"[{label}]({url})"
        # WhatsApp, Instagram, Messenger, Viber, LINE -- raw URL
        return f"{label}: {url}"

    def _stars(self, rating: float) -> str:
        """Return a star string like stars filled/empty."""
        filled = int(rating)
        filled = max(0, min(5, filled))
        return "\u2605" * filled + "\u2606" * (5 - filled)

    def _format_reviews(self, count: int) -> str:
        """Format review count: 1200 -> '1.2K', 1500000 -> '1.5M'."""
        if count >= 1_000_000:
            return f"{count / 1_000_000:.1f}M"
        if count >= 1_000:
            return f"{count / 1_000:.1f}K"
        return str(count)

    def _truncate(self, text: str) -> str:
        """Truncate text to the platform caption limit."""
        limit = self.max_caption_length
        if len(text) <= limit:
            return text
        truncated = text[: limit - 20]
        last_nl = truncated.rfind("\n")
        if last_nl > limit // 2:
            truncated = truncated[:last_nl]
        return truncated + "\n..."

    # -- Public methods --------------------------------------------------------

    def welcome(self) -> str:
        """Welcome message."""
        return self._esc(t("welcome", lang=self.lang))

    def help_text(self) -> str:
        """Help / how-to-use message."""
        return self._esc(t("help", lang=self.lang))

    def loading_vision(self) -> str:
        """Loading message for photo analysis (hourglass emoji)."""
        return "\u231b " + self._esc(t("loading_vision", lang=self.lang))

    def loading_search(self) -> str:
        """Loading message for Amazon search (magnifying glass emoji)."""
        return "\U0001f50d " + self._esc(t("loading_search", lang=self.lang))

    def language_picker(self) -> str:
        """Prompt for the language selection menu."""
        return self._esc(t("choose_language", lang=self.lang))

    def product_picker(self, products: list) -> str:
        """Format the multi-product picker message."""
        from image_analyzer import ProductInfo
        count = len(products)
        lines = [self._esc(t("pick_product", lang=self.lang, count=count))]
        lines.append("")
        for i, p in enumerate(products):
            name = p.product_name if isinstance(p, ProductInfo) else str(p)
            lines.append(f"{i + 1}\. {self._bold(self._esc(name))}")
        lines.append("")
        lines.append(self._italic(self._esc(t("pick_product_hint", lang=self.lang))))
        return "\n".join(lines)
    def text_search_loading(self, query: str) -> str:
        """Loading message for text-based search."""
        return self._esc(t("text_search_prompt", lang=self.lang, query=query))

    def error(self, key: str) -> str:
        """Warning emoji + translated error by key."""
        return "\u26a0\ufe0f " + self._esc(t(key, lang=self.lang))

    # -- Identification card ---------------------------------------------------

    def identification_card(
        self,
        result: ProviderResult,
        is_admin: bool = False,
    ) -> str:
        """Format an AI identification result card.

        Shows product title, brand/category, key features (up to 4),
        confidence with color emoji, and search query.
        Admin-only: provider name, cost, latency.
        """
        product_name = getattr(result, "product_name", "Unknown")
        brand = getattr(result, "brand", None) or "Unknown"
        category = getattr(result, "category", "")
        key_features = getattr(result, "key_features", [])
        confidence = getattr(result, "confidence", "medium")
        search_query = getattr(result, "amazon_search_query", "")
        provider_name = getattr(result, "provider_name", "")
        cost_usd = getattr(result, "cost_usd", None)
        latency_ms = getattr(result, "latency_ms", None)

        conf_emoji = _CONF_EMOJI.get(confidence, "\u26aa")
        conf_label = t(f"confidence_{confidence}", lang=self.lang)

        features = key_features[:4]

        lines: list[str] = []
        lines.append("\u2728 " + self._bold(self._esc(product_name)))
        lines.append("")
        lines.append(
            "\U0001f3e2 " + self._esc(brand)
            + "  \u00b7  \U0001f4e6 " + self._esc(category)
        )
        lines.append(
            conf_emoji + " "
            + self._esc(t("id_confidence", lang=self.lang)) + ": "
            + self._bold(self._esc(conf_label))
        )
        lines.append("")

        if features:
            lines.append(self._bold(self._esc(t("id_features", lang=self.lang))))
            for feat in features:
                lines.append("  \u25b8 " + self._esc(feat))
            lines.append("")

        lines.append(
            "\U0001f50e " + self._esc(t("id_search_query", lang=self.lang))
            + ": " + self._esc(search_query)
        )

        # Admin-only section
        if is_admin:
            admin_parts = ["\U0001f916 " + self._esc(provider_name)]
            if cost_usd is not None:
                if cost_usd < 0.001:
                    cost_str = f"${cost_usd * 1000:.3f}m"
                else:
                    cost_str = f"${cost_usd:.4f}"
                admin_parts.append("\U0001f4b8 " + self._esc(cost_str))
            if latency_ms is not None:
                admin_parts.append("\u26a1 " + self._esc(f"{latency_ms}ms"))
            lines.append("")
            lines.append(self._italic("  ".join(admin_parts)))

        return "\n".join(lines)

    # -- Product caption -------------------------------------------------------

    def product_caption(
        self,
        item: AmazonItem,
        index: int,
        total: int,
        short_url: str | None = None,
        israel_status: str | None = None,
        price_history=None,
        is_admin: bool = False,
    ) -> str:
        """Format a single Amazon product caption.

        Shows title (truncated to 80 chars), price, star rating with
        review count, badges (Prime, Sold by Amazon), Israel shipping
        status, shop link, and counter.
        Truncated to the platform caption limit.
        """
        # Title -- truncated to 80 chars
        raw_title = getattr(item, "title", "")[:80]
        title = self._bold(self._esc(raw_title))

        # Price
        price_usd = getattr(item, "price_usd", None)
        if price_usd is not None:
            price_str = f"${price_usd:.2f}"
        else:
            price_str = "-"
        price_line = "\U0001f4b0 " + self._esc(price_str)

        # Rating + reviews
        rating = getattr(item, "rating", None)
        review_count = getattr(item, "review_count", None)
        if rating is not None:
            stars = self._stars(rating)
            rating_part = self._esc(stars) + " " + self._esc(f"{rating}")
            if review_count:
                rating_part += " \(" + self._esc(self._format_reviews(review_count)) + "\)"
        else:
            rating_part = "-"
        rating_line = "\u2b50 " + rating_part

        # Badges
        badges: list[str] = []
        if getattr(item, "is_prime", False):
            badges.append(t("product_prime", lang=self.lang))
        if getattr(item, "is_sold_by_amazon", False):
            badges.append(t("product_sold_by", lang=self.lang) + " Amazon")
        badge_line = "  ".join(badges) if badges else ""

        # Price history
        price_hist_line = ""
        if price_history:
            ph_parts = []
            avg_90d = getattr(price_history, "avg_90d", None)
            low_all_time = getattr(price_history, "low_all_time", None)
            if avg_90d is not None:
                ph_parts.append(self._esc(f"90d avg: ${avg_90d:.2f}"))
            if low_all_time is not None:
                ph_parts.append(self._esc(f"Low: ${low_all_time:.2f}"))
            if ph_parts:
                price_hist_line = "\U0001f4c9 " + " \| ".join(ph_parts)
                deal_label = getattr(price_history, "deal_label", "")
                if deal_label:
                    price_hist_line += "\n   " + self._esc(deal_label)

        # Israel shipping status
        israel_line = ""
        if israel_status == "yes":
            israel_line = "\U0001f1ee\U0001f1f1 " + self._esc(
                t("product_israel_yes", lang=self.lang)
            )
        elif israel_status == "no":
            israel_line = "\U0001f1ee\U0001f1f1 " + self._esc(
                t("product_israel_no", lang=self.lang)
            )
        elif israel_status == "free":
            israel_line = "\U0001f1ee\U0001f1f1 " + self._esc(
                t("product_israel_free", lang=self.lang)
            )
        elif israel_status == "checking":
            israel_line = "\U0001f1ee\U0001f1f1 " + self._esc(
                t("product_israel_checking", lang=self.lang)
            )

        # Shop link
        url = short_url or getattr(item, "url", "") or ""
        if url:
            shop_line = "\U0001f6d2 " + self._link(
                t("btn_shop", lang=self.lang), url
            )
        else:
            shop_line = ""

        # Counter
        counter = t("product_counter", lang=self.lang, current=index, total=total)

        # Assemble
        parts = [title, "", price_line + "  " + rating_line]
        if badge_line:
            parts.append(badge_line)
        if price_hist_line:
            parts.append(price_hist_line)
        if israel_line:
            parts.append(israel_line)
        if shop_line:
            parts.append(shop_line)
        parts.append("")
        parts.append(counter)

        caption = "\n".join(parts)
        return self._truncate(caption)
