"""
Telegram adapter — implements PlatformAdapter using python-telegram-bot v20+.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Awaitable

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
from adapters.base import Button, MessageRef, PlatformAdapter

logger = logging.getLogger(__name__)


class TelegramAdapter(PlatformAdapter):
    """PlatformAdapter implementation for Telegram via python-telegram-bot."""

    # ── Capability overrides ───────────────────────────────────────────────
    platform_name: str = "telegram"
    max_caption_length: int = 1024
    max_message_length: int = 4096
    supports_photo_edit: bool = True
    supports_inline_buttons: bool = True
    max_buttons_per_row: int = 8
    supports_carousels: bool = False

    def __init__(
        self,
        on_photo: Callable[["TelegramAdapter", Any], Awaitable[None]],
        on_callback: Callable[["TelegramAdapter", str, str, str, Any], Awaitable[None]],
        on_text: Callable[["TelegramAdapter", str, str, str, Any], Awaitable[None]],
        on_command: Callable[["TelegramAdapter", str, str, str, str, Any], Awaitable[None]],
    ) -> None:
        self._on_photo = on_photo
        self._on_callback = on_callback
        self._on_text = on_text
        self._on_command = on_command
        self._app: Application | None = None

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        token = config.TELEGRAM_BOT_TOKEN
        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")

        from admin import get_admin_handlers

        self._app = Application.builder().token(token).build()
        app = self._app

        # Admin handlers must be registered BEFORE generic handlers
        for handler in get_admin_handlers():
            app.add_handler(handler)

        # Command handlers
        app.add_handler(CommandHandler("start", self._handle_command))
        app.add_handler(CommandHandler("help", self._handle_command))
        app.add_handler(CommandHandler("language", self._handle_command))
        app.add_handler(CommandHandler("providers", self._handle_command))
        app.add_handler(CommandHandler("setloggroup", self._handle_command))
        app.add_handler(CommandHandler("webtoken", self._handle_command))

        # Photo handler
        app.add_handler(MessageHandler(filters.PHOTO, self._handle_photo))

        # Callback query handler
        app.add_handler(CallbackQueryHandler(self._handle_callback))

        # Text handler (non-command text)
        app.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND, self._handle_text
        ))

        # Group message handler for log group setup (must be last)
        app.add_handler(MessageHandler(
            filters.ALL & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP),
            self._handle_group_message,
        ), group=1)  # group=1 so it runs in a separate handler group

        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)

        # Set bot command menu
        await app.bot.set_my_commands([
            BotCommand("start", "Start the bot"),
            BotCommand("help", "Show help"),
            BotCommand("language", "Change language"),
            BotCommand("admin", "Open admin panel"),
        ])

        logger.info("TelegramAdapter started (polling)")

    async def stop(self) -> None:
        if self._app is None:
            return
        if self._app.updater and self._app.updater.running:
            await self._app.updater.stop()
        if self._app.running:
            await self._app.stop()
        await self._app.shutdown()
        logger.info("TelegramAdapter stopped")

    # ── PTB handler wrappers (private) ─────────────────────────────────────

    async def _handle_group_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Intercept group messages for log group setup."""
        if not update.effective_chat or not update.effective_user:
            return
        chat_type = update.effective_chat.type
        if chat_type not in ("group", "supergroup"):
            return

        user_id = update.effective_user.id
        chat_id = str(update.effective_chat.id)

        import log_group
        if not log_group.is_listening(user_id):
            return

        log_group.stop_listening(user_id)
        log_group.set_group(chat_id)

        # Confirm in the group
        await update.effective_chat.send_message(
            "\u2705 This group is now the log group. All bot actions will be logged here."
        )

        # Also notify the admin privately
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"\u2705 Log group set to: {update.effective_chat.title} (ID: {chat_id})"
            )
        except Exception:
            pass

    async def _handle_photo(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        await self._on_photo(self, update)

    async def _handle_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        await query.answer()
        user_id = str(query.from_user.id)
        chat_id = str(query.message.chat_id)
        data = query.data or ""
        await self._on_callback(self, user_id, chat_id, data, update)

    async def _handle_text(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        user_id = str(update.message.from_user.id)
        chat_id = str(update.message.chat_id)
        text = update.message.text or ""
        await self._on_text(self, user_id, chat_id, text, update)

    async def _handle_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        message = update.message
        text = (message.text or "").strip()
        # Parse /command args — first token is the command
        parts = text.split(maxsplit=1)
        command = parts[0].lstrip("/").split("@")[0]  # strip leading / and @botname
        args = parts[1] if len(parts) > 1 else ""
        user_id = str(message.from_user.id)
        chat_id = str(message.chat_id)
        await self._on_command(self, user_id, chat_id, command, args, update)

    # ── Incoming helpers ───────────────────────────────────────────────────

    async def download_photo(self, event: Any) -> bytes:
        update: Update = event
        photo = update.message.photo[-1]  # highest resolution
        file = await photo.get_file()
        data = await file.download_as_bytearray()
        return bytes(data)

    def get_user_id(self, event: Any) -> str:
        update: Update = event
        if update.message and update.message.from_user:
            return str(update.message.from_user.id)
        if update.callback_query and update.callback_query.from_user:
            return str(update.callback_query.from_user.id)
        raise ValueError("Cannot extract user_id from update")

    def get_platform_user_id(self, event: Any) -> str:
        return f"telegram:{self.get_user_id(event)}"

    # ── Outgoing: text ─────────────────────────────────────────────────────

    async def send_text(
        self,
        chat_id: str,
        text: str,
        buttons: list[list[Button]] | None = None,
    ) -> MessageRef:
        markup = self._build_keyboard(buttons) if buttons else None
        msg = await self._app.bot.send_message(
            chat_id=int(chat_id),
            text=text,
            parse_mode="MarkdownV2",
            reply_markup=markup,
        )
        return MessageRef(
            platform="telegram",
            chat_id=str(msg.chat_id),
            message_id=str(msg.message_id),
            raw=msg,
        )

    async def edit_text(
        self,
        ref: MessageRef,
        text: str,
        buttons: list[list[Button]] | None = None,
    ) -> None:
        markup = self._build_keyboard(buttons) if buttons else None
        await self._app.bot.edit_message_text(
            chat_id=int(ref.chat_id),
            message_id=int(ref.message_id),
            text=text,
            parse_mode="MarkdownV2",
            reply_markup=markup,
        )

    # ── Outgoing: photo ────────────────────────────────────────────────────

    async def send_photo(
        self,
        chat_id: str,
        image: str | bytes,
        caption: str = "",
        buttons: list[list[Button]] | None = None,
    ) -> MessageRef:
        markup = self._build_keyboard(buttons) if buttons else None
        msg = await self._app.bot.send_photo(
            chat_id=int(chat_id),
            photo=image,
            caption=caption or None,
            parse_mode="MarkdownV2",
            reply_markup=markup,
        )
        return MessageRef(
            platform="telegram",
            chat_id=str(msg.chat_id),
            message_id=str(msg.message_id),
            raw=msg,
        )

    async def edit_photo(
        self,
        ref: MessageRef,
        image: str | bytes,
        caption: str = "",
        buttons: list[list[Button]] | None = None,
    ) -> None:
        markup = self._build_keyboard(buttons) if buttons else None
        media = InputMediaPhoto(
            media=image,
            caption=caption or None,
            parse_mode="MarkdownV2",
        )
        await self._app.bot.edit_message_media(
            chat_id=int(ref.chat_id),
            message_id=int(ref.message_id),
            media=media,
            reply_markup=markup,
        )

    # ── Outgoing: delete ───────────────────────────────────────────────────

    async def delete_message(self, ref: MessageRef) -> None:
        await self._app.bot.delete_message(
            chat_id=int(ref.chat_id),
            message_id=int(ref.message_id),
        )

    # ── Keyboard builder ───────────────────────────────────────────────────

    @staticmethod
    def _build_keyboard(
        buttons: list[list[Button]],
    ) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        for row in buttons:
            kb_row: list[InlineKeyboardButton] = []
            for btn in row:
                if btn.url:
                    kb_row.append(InlineKeyboardButton(
                        text=btn.label, url=btn.url,
                    ))
                else:
                    kb_row.append(InlineKeyboardButton(
                        text=btn.label, callback_data=btn.callback_data or "",
                    ))
            rows.append(kb_row)
        return InlineKeyboardMarkup(rows)
