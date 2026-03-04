"""
Discord adapter — implements PlatformAdapter using discord.py.

Unlike Meta-based adapters, Discord uses a WebSocket gateway connection
rather than webhooks.
"""
from __future__ import annotations

import asyncio
import io
import logging
from typing import Any, Callable, Awaitable

import discord
from discord import ui

import config
from adapters.base import Button, MessageRef, PlatformAdapter

logger = logging.getLogger(__name__)


class _CallbackButton(ui.Button):
    """A discord.ui.Button that fires the adapter callback on click."""

    def __init__(
        self,
        *,
        label: str,
        custom_id: str,
        adapter: "DiscordAdapter",
    ) -> None:
        super().__init__(
            label=label,
            custom_id=custom_id,
            style=discord.ButtonStyle.primary,
        )
        self._adapter = adapter

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        user_id = str(interaction.user.id)
        chat_id = str(interaction.channel_id)
        event = {
            "interaction": interaction,
            "user_id": user_id,
            "chat_id": chat_id,
        }
        await self._adapter._on_callback(
            self._adapter, user_id, chat_id, self.custom_id, event,
        )


class _LinkButton(ui.Button):
    """A discord.ui.Button that opens a URL."""

    def __init__(self, *, label: str, url: str) -> None:
        super().__init__(label=label, url=url, style=discord.ButtonStyle.link)


class DiscordAdapter(PlatformAdapter):
    """PlatformAdapter implementation for Discord via discord.py."""

    # ── Capability overrides ───────────────────────────────────────────────
    platform_name: str = "discord"
    max_caption_length: int = 4096
    max_message_length: int = 2000
    supports_photo_edit: bool = True
    supports_inline_buttons: bool = True
    max_buttons_per_row: int = 5
    supports_carousels: bool = False

    def __init__(
        self,
        on_photo: Callable[["DiscordAdapter", Any], Awaitable[None]],
        on_callback: Callable[["DiscordAdapter", str, str, str, Any], Awaitable[None]],
        on_text: Callable[["DiscordAdapter", str, str, str, Any], Awaitable[None]],
        on_command: Callable[["DiscordAdapter", str, str, str, str, Any], Awaitable[None]],
    ) -> None:
        self._on_photo = on_photo
        self._on_callback = on_callback
        self._on_text = on_text
        self._on_command = on_command

        self._token: str = getattr(config, "DISCORD_TOKEN", "")
        self._client: discord.Client | None = None
        self._task: asyncio.Task | None = None

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        intents = discord.Intents.default()
        intents.messages = True
        intents.message_content = True
        self._client = discord.Client(intents=intents)

        @self._client.event
        async def on_message(message: discord.Message) -> None:
            # Ignore messages from the bot itself
            if message.author == self._client.user:
                return
            await self._process_message(message)

        @self._client.event
        async def on_ready() -> None:
            logger.info("Discord adapter connected as %s", self._client.user)

        # Run the client in a background task
        self._task = asyncio.create_task(self._client.start(self._token))
        logger.info("Discord adapter starting...")

    async def stop(self) -> None:
        if self._client:
            await self._client.close()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        logger.info("Discord adapter stopped.")

    # ── Internal message routing ───────────────────────────────────────────

    async def _process_message(self, message: discord.Message) -> None:
        """Route an incoming Discord message to the appropriate callback."""
        user_id = str(message.author.id)
        chat_id = str(message.channel.id)

        event = {
            "message": message,
            "user_id": user_id,
            "chat_id": chat_id,
        }

        # Check for image attachments
        for attachment in message.attachments:
            if attachment.content_type and attachment.content_type.startswith("image/"):
                event["image_url"] = attachment.url
                await self._on_photo(self, event)
                return

        text = message.content
        if text.startswith("!") or text.startswith("/"):
            parts = text.split(maxsplit=1)
            command = parts[0]
            args = parts[1] if len(parts) > 1 else ""
            await self._on_command(self, user_id, chat_id, command, args, event)
        elif text:
            await self._on_text(self, user_id, chat_id, text, event)

    # ── Incoming helpers ───────────────────────────────────────────────────

    async def download_photo(self, event: Any) -> bytes:
        image_url = event.get("image_url", "")
        msg: discord.Message = event["message"]
        if image_url:
            for attachment in msg.attachments:
                if attachment.url == image_url:
                    return await attachment.read()
        # Fallback: try first image attachment
        for attachment in msg.attachments:
            if attachment.content_type and attachment.content_type.startswith("image/"):
                return await attachment.read()
        raise ValueError("No image in event")

    def get_user_id(self, event: Any) -> str:
        return event["user_id"]

    def get_platform_user_id(self, event: Any) -> str:
        return f"discord:{event['user_id']}"

    def get_chat_id(self, event: Any) -> str:
        return event["chat_id"]

    # ── View builder ───────────────────────────────────────────────────────

    def _build_view(self, buttons: list[list[Button]] | None) -> ui.View | None:
        """Build a discord.ui.View from button rows."""
        if not buttons:
            return None
        view = ui.View(timeout=None)
        for row in buttons:
            for btn in row:
                if btn.url:
                    view.add_item(_LinkButton(label=btn.label, url=btn.url))
                else:
                    view.add_item(
                        _CallbackButton(
                            label=btn.label,
                            custom_id=btn.callback_data or btn.label,
                            adapter=self,
                        )
                    )
        return view

    async def _get_channel(self, chat_id: str) -> discord.abc.Messageable:
        """Fetch a Discord channel by ID."""
        assert self._client is not None
        channel = self._client.get_channel(int(chat_id))
        if channel is None:
            channel = await self._client.fetch_channel(int(chat_id))
        return channel  # type: ignore[return-value]

    # ── Outgoing: text ─────────────────────────────────────────────────────

    async def send_text(
        self,
        chat_id: str,
        text: str,
        buttons: list[list[Button]] | None = None,
    ) -> MessageRef:
        channel = await self._get_channel(chat_id)
        view = self._build_view(buttons)
        kwargs: dict[str, Any] = {"content": text}
        if view:
            kwargs["view"] = view
        msg = await channel.send(**kwargs)
        return MessageRef(
            platform="discord",
            chat_id=chat_id,
            message_id=str(msg.id),
            raw=msg,
        )

    async def edit_text(
        self,
        ref: MessageRef,
        text: str,
        buttons: list[list[Button]] | None = None,
    ) -> None:
        msg: discord.Message = ref.raw
        view = self._build_view(buttons)
        kwargs: dict[str, Any] = {"content": text}
        if view:
            kwargs["view"] = view
        await msg.edit(**kwargs)

    # ── Outgoing: photo ────────────────────────────────────────────────────

    async def send_photo(
        self,
        chat_id: str,
        image: str | bytes,
        caption: str = "",
        buttons: list[list[Button]] | None = None,
    ) -> MessageRef:
        channel = await self._get_channel(chat_id)
        view = self._build_view(buttons)

        if isinstance(image, str):
            embed = discord.Embed()
            embed.set_image(url=image)
            if caption:
                embed.description = caption[:self.max_caption_length]
            kwargs: dict[str, Any] = {"embed": embed}
            if view:
                kwargs["view"] = view
            msg = await channel.send(**kwargs)
        else:
            file = discord.File(fp=io.BytesIO(image), filename="photo.jpg")
            embed = discord.Embed()
            embed.set_image(url="attachment://photo.jpg")
            if caption:
                embed.description = caption[:self.max_caption_length]
            kwargs = {"embed": embed, "file": file}
            if view:
                kwargs["view"] = view
            msg = await channel.send(**kwargs)

        return MessageRef(
            platform="discord",
            chat_id=chat_id,
            message_id=str(msg.id),
            raw=msg,
        )

    async def edit_photo(
        self,
        ref: MessageRef,
        image: str | bytes,
        caption: str = "",
        buttons: list[list[Button]] | None = None,
    ) -> None:
        msg: discord.Message = ref.raw
        view = self._build_view(buttons)

        embed = discord.Embed()
        if isinstance(image, str):
            embed.set_image(url=image)
        else:
            embed.set_image(url="attachment://photo.jpg")
        if caption:
            embed.description = caption[:self.max_caption_length]

        kwargs: dict[str, Any] = {"embed": embed}
        if view:
            kwargs["view"] = view
        if isinstance(image, bytes):
            kwargs["attachments"] = [
                discord.File(fp=io.BytesIO(image), filename="photo.jpg")
            ]
        await msg.edit(**kwargs)

    # ── Outgoing: delete ───────────────────────────────────────────────────

    async def delete_message(self, ref: MessageRef) -> None:
        msg: discord.Message = ref.raw
        await msg.delete()
