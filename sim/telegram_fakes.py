"""Minimal fakes for the python-telegram-bot surface bot.py actually uses.

Verified against current bot.py by grep (re-verify after any rebase):
    update.effective_chat.id, update.effective_user.id,
    update.message.text/.voice/.audio/.reply_text/.delete,
    update.callback_query.data/.answer/.message/.edit_message_text,
    context.args/.bot/.error, context.bot.get_file/.send_message/
    .send_document/.send_photo, query.message.chat_id.

Delta vs. the reference implementation's sim/telegram_fakes.py: this bot's
_check_access gates on update.effective_user.id (ALLOWED_USERS), not just
effective_chat — FakeUpdate carries both (same int, private-chat convention:
chat_id == user_id, matching docs/architecture.md's multi-user model). This bot also has
no per-chat context.user_data usage (state lives in bot.active_sessions,
keyed by chat_id, not in PTB context) and no InlineKeyboardMarkup edits via
edit_message_reply_markup (only edit_message_text) — so those reference
surfaces are dropped here as unused, not carried over unverified.

Handlers are driven by direct call (start_command, message_handler,
callback_handler, ...) — handler-registration fidelity is not the test
target. All sends are captured into a per-chat transcript with virtual-clock
timestamps; buttons keep their callback_data so scenarios can press them.
bot.py constructs real InlineKeyboardMarkup objects; we only read them.
"""

from __future__ import annotations

from typing import Any, Callable


def _buttons_of(reply_markup) -> list[list[dict]]:
    if reply_markup is None:
        return []
    rows = []
    for row in reply_markup.inline_keyboard:
        rows.append([{"text": b.text, "callback_data": b.callback_data} for b in row])
    return rows


class Transcript:
    """Chronological per-chat event log. Events are plain dicts (JSON-safe)."""

    def __init__(self, now_fn: Callable[[], Any]):
        self._now = now_fn
        self.events: list[dict] = []

    def record(self, chat: str, direction: str, kind: str, **fields) -> dict:
        event = {"t": self._now().isoformat(), "chat": chat,
                 "dir": direction, "kind": kind, **fields}
        self.events.append(event)
        return event

    def outbound(self, chat: str) -> list[dict]:
        return [e for e in self.events
                if e["chat"] == chat and e["dir"] == "out"]

    def new_outbound(self, chat: str, cursor: int) -> tuple[list[dict], int]:
        """Outbound events for chat with index > cursor; returns (events, new_cursor)."""
        out = [(i, e) for i, e in enumerate(self.events)
               if i > cursor and e["chat"] == chat and e["dir"] == "out"]
        if not out:
            return [], cursor
        return [e for _, e in out], out[-1][0]


class FakeChat:
    def __init__(self, chat_id: int):
        self.id = chat_id
        # bot._check_access rejects non-private chats since the PRIV-011 fix
        # (2026-08-04); the sim models exactly the private-chat convention.
        self.type = "private"


class FakeUser:
    def __init__(self, user_id: int):
        self.id = user_id


class FakeTelegramFile:
    """Stands in for the object returned by context.bot.get_file(file_id)."""

    def __init__(self, data: bytes):
        self._data = data

    async def download_to_memory(self, buf) -> None:
        buf.write(self._data)


class FakeBot:
    """Stands in for context.bot (this bot has no module-level `app.bot`;
    every handler receives its Bot instance via context or as a parameter)."""

    def __init__(self, transcript: Transcript, chat_name: Callable[[int], str],
                 voice_transcripts: dict[str, bytes] | None = None):
        self._transcript = transcript
        self._chat_name = chat_name
        # file_id -> raw bytes, so a scenario can register what a voice
        # message "contains" before send_voice(); harness wires the actual
        # transcription text separately via llm_client.transcribe_audio.
        self._voice_files = voice_transcripts or {}

    async def send_message(self, chat_id: int, text: str, reply_markup=None,
                           parse_mode=None, **_kw):
        return self._transcript.record(
            self._chat_name(chat_id), "out", "text", text=text,
            buttons=_buttons_of(reply_markup), parse_mode=parse_mode)

    async def send_chat_action(self, chat_id: int, action=None, **_kw):
        return self._transcript.record(
            self._chat_name(chat_id), "out", "chat_action", action=str(action))

    async def send_document(self, chat_id: int, document=None, filename=None,
                            caption=None, **_kw):
        size = None
        if document is not None and hasattr(document, "getbuffer"):
            size = document.getbuffer().nbytes
        return self._transcript.record(
            self._chat_name(chat_id), "out", "document",
            filename=filename or getattr(document, "name", None),
            caption=caption, size_bytes=size)

    async def send_photo(self, chat_id: int, photo=None, caption=None, **_kw):
        size = None
        if photo is not None and hasattr(photo, "getbuffer"):
            size = photo.getbuffer().nbytes
        return self._transcript.record(
            self._chat_name(chat_id), "out", "photo",
            caption=caption, size_bytes=size)

    async def get_file(self, file_id):
        data = self._voice_files.get(file_id)
        if data is None:
            raise NotImplementedError(
                f"no fake audio registered for file_id={file_id!r}; "
                "register it via harness voice-message helpers before sending")
        return FakeTelegramFile(data)


class FakeVoice:
    """Stands in for update.message.voice / .audio."""

    def __init__(self, file_id: str, duration: int = 5):
        self.file_id = file_id
        self.duration = duration


class FakeMessage:
    def __init__(self, harness_bot: FakeBot, transcript: Transcript,
                 chat_id: int, chat_name: str, text: str | None = None,
                 voice: FakeVoice | None = None, audio: FakeVoice | None = None):
        self._bot = harness_bot
        self._transcript = transcript
        self.chat_id = chat_id
        self._chat_name = chat_name
        self.text = text
        self.voice = voice
        self.audio = audio

    async def reply_text(self, text: str, reply_markup=None, parse_mode=None, **_kw):
        return await self._bot.send_message(
            self.chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)

    async def reply_document(self, document=None, caption=None, **_kw):
        return await self._bot.send_document(
            self.chat_id, document=document, caption=caption)

    async def delete(self):
        self._transcript.record(self._chat_name, "meta", "delete_message",
                                text=self.text)


class FakeCallbackQuery:
    def __init__(self, message: FakeMessage, data: str, transcript: Transcript,
                 chat_name: str):
        self.message = message
        self.data = data
        self._transcript = transcript
        self._chat_name = chat_name

    async def answer(self, *_a, **_kw):
        pass

    async def edit_message_text(self, text: str, parse_mode=None, reply_markup=None, **_kw):
        self._transcript.record(self._chat_name, "out", "edit_text",
                                text=text, parse_mode=parse_mode,
                                buttons=_buttons_of(reply_markup))


class FakeUpdate:
    def __init__(self, chat_id: int, user_id: int | None = None,
                 message: FakeMessage | None = None,
                 callback_query: FakeCallbackQuery | None = None):
        self.effective_chat = FakeChat(chat_id)
        self.effective_user = FakeUser(user_id if user_id is not None else chat_id)
        self.message = message
        self.callback_query = callback_query
        self.error = None


class FakeContext:
    """One per chat, persistent across updates (like PTB's per-chat context)."""

    def __init__(self, bot: FakeBot):
        self.bot = bot
        self.args: list[str] = []
        self.error = None
