"""
Abstract base class for platform adapters.

Every messaging platform (Telegram, WhatsApp, Discord, etc.) implements
this interface so the core bot logic stays platform-agnostic.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── Data types ─────────────────────────────────────────────────────────────────

@dataclass
class MessageRef:
    """Reference to a sent message, used for edits and deletes."""
    platform: str               # e.g. "telegram", "whatsapp", "discord"
    chat_id: str                # platform-specific chat/channel identifier
    message_id: str             # platform-specific message identifier
    raw: Any = field(default=None, repr=False)  # original platform response object


@dataclass
class Button:
    """A single inline button attached to a message."""
    label: str
    url: str | None = None
    callback_data: str | None = None


@dataclass
class CarouselItem:
    """One card in a multi-product carousel."""
    image_url: str
    title: str
    subtitle: str = ""
    url: str | None = None
    buttons: list[Button] = field(default_factory=list)


# ── Abstract adapter ───────────────────────────────────────────────────────────

class PlatformAdapter(ABC):
    """
    Base class all platform adapters must implement.

    Capability flags let the core bot adapt its formatting and behaviour
    to each platform's constraints (e.g. Telegram supports photo edits,
    WhatsApp does not).
    """

    # ── Capability flags (override in subclasses) ──────────────────────────

    platform_name: str = "unknown"
    max_caption_length: int = 1024
    max_message_length: int = 4096
    supports_photo_edit: bool = False
    supports_inline_buttons: bool = True
    max_buttons_per_row: int = 3
    supports_carousels: bool = False

    # ── Lifecycle ──────────────────────────────────────────────────────────

    @abstractmethod
    async def start(self) -> None:
        """Initialise the adapter (connect, authenticate, start polling/webhook)."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully shut down the adapter."""
        ...

    # ── Incoming helpers ───────────────────────────────────────────────────

    @abstractmethod
    async def download_photo(self, event: Any) -> bytes:
        """Download the photo from an incoming message/event and return raw bytes."""
        ...

    @abstractmethod
    def get_user_id(self, event: Any) -> str:
        """Return a platform-agnostic internal user ID string for the sender."""
        ...

    @abstractmethod
    def get_platform_user_id(self, event: Any) -> str:
        """Return the raw platform-specific user ID string for the sender."""
        ...

    def get_chat_id(self, event: Any) -> str:
        """Return the chat/conversation ID for the event. Defaults to user ID."""
        return self.get_user_id(event)

    # ── Outgoing: text ─────────────────────────────────────────────────────

    @abstractmethod
    async def send_text(
        self,
        chat_id: str,
        text: str,
        buttons: list[list[Button]] | None = None,
    ) -> MessageRef:
        """Send a text message, optionally with inline button rows."""
        ...

    @abstractmethod
    async def edit_text(
        self,
        ref: MessageRef,
        text: str,
        buttons: list[list[Button]] | None = None,
    ) -> None:
        """Edit the text (and optionally buttons) of an existing message."""
        ...

    # ── Outgoing: photo ────────────────────────────────────────────────────

    @abstractmethod
    async def send_photo(
        self,
        chat_id: str,
        image: str | bytes,
        caption: str = "",
        buttons: list[list[Button]] | None = None,
    ) -> MessageRef:
        """
        Send a photo message.

        `image` is either a URL string or raw bytes.
        """
        ...

    async def edit_photo(
        self,
        ref: MessageRef,
        image: str | bytes,
        caption: str = "",
        buttons: list[list[Button]] | None = None,
    ) -> None:
        """
        Edit an existing photo message.

        Default raises NotImplementedError — only platforms that set
        supports_photo_edit = True need to override this.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support photo editing"
        )

    # ── Outgoing: carousel ─────────────────────────────────────────────────

    async def send_carousel(
        self,
        chat_id: str,
        items: list[CarouselItem],
    ) -> list[MessageRef]:
        """
        Send a product carousel.

        Default implementation sends each item as a separate photo message.
        Platforms with native carousel support (e.g. Facebook Messenger)
        should override this.
        """
        refs: list[MessageRef] = []
        for item in items:
            buttons_row = [[Button(label=b.label, url=b.url, callback_data=b.callback_data) for b in item.buttons]] if item.buttons else None
            caption = item.title
            if item.subtitle:
                caption += f"\n{item.subtitle}"
            ref = await self.send_photo(
                chat_id=chat_id,
                image=item.image_url,
                caption=caption,
                buttons=buttons_row,
            )
            refs.append(ref)
        return refs

    # ── Outgoing: delete ───────────────────────────────────────────────────

    @abstractmethod
    async def delete_message(self, ref: MessageRef) -> None:
        """Delete a previously sent message."""
        ...
