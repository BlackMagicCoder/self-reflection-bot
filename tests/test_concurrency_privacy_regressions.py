"""Concurrency and privacy regressions (found in a code review on 2026-09-04
and its follow-ups).

Six defects, four of them consequences of enabling .concurrent_updates(True)
or of the per-user provider selection. Each test here fails against the code as
it stood before the fix — that is the bar, not "the feature still works".
"""
import asyncio
import threading
from unittest.mock import AsyncMock, Mock

import pytest
from cryptography.fernet import Fernet

import bot
import crypto
import llm_client
import memory

USER = 909
_KEYS: dict[int, str] = {}


def _key_for(user_id: int) -> str:
    """The plaintext key this module handed to crypto.set_key — crypto keeps
    only the Fernet object, and the eval CLI needs the string back."""
    return _KEYS[user_id]


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    monkeypatch.setattr(bot, "ALLOWED_USERS", set())
    _KEYS[USER] = Fernet.generate_key().decode()
    crypto.set_key(USER, _KEYS[USER])
    yield
    crypto.clear_key(USER)
    bot.active_sessions.pop(USER, None)
    bot._chat_locks.pop(USER, None)


def _msg_update():
    u = Mock()
    u.effective_chat = Mock(id=USER, type="private")
    u.effective_user = None
    u.message = AsyncMock()
    return u


# --- 1. /privacy must survive a locked user ---------------------------------

def test_privacy_works_while_locked():
    """/privacy is deliberately exempt from the lock gate so a locked user can
    reach the Art. 50 notice — and after EVERY restart every user is locked.
    Resolving the provider decrypted the profile, so it raised for exactly the
    population the exemption exists for, and they got no reply at all."""
    memory.save_user_profile(USER, {"name": "Alex", "onboarding_complete": True})
    crypto.clear_key(USER)
    upd = _msg_update()
    asyncio.run(bot.privacy_command(upd, Mock()))
    text = upd.message.reply_text.await_args.args[0]
    assert "Datenschutzhinweis" in text


def test_privacy_while_locked_says_the_choice_could_not_be_read():
    """An incomplete notice is acceptable; a false one is not. A locked user
    sees the server default, so the notice must say so rather than assert a
    vendor that may not be theirs."""
    memory.save_user_profile(USER, {"name": "Alex", "onboarding_complete": True})
    crypto.clear_key(USER)
    upd = _msg_update()
    asyncio.run(bot.privacy_command(upd, Mock()))
    assert "gesperrt" in upd.message.reply_text.await_args.args[0]


def test_privacy_for_a_brand_new_user_has_no_caveat():
    """Someone with no data yet genuinely IS on the default — the caveat would
    be noise on the consent screen, which is the notice that matters most."""
    crypto.clear_key(USER)
    upd = _msg_update()
    asyncio.run(bot.privacy_command(upd, Mock()))
    assert "gesperrt" not in upd.message.reply_text.await_args.args[0]


def test_user_provider_is_lock_safe():
    memory.save_user_profile(USER, {"llm_provider": "nanogpt"})
    crypto.clear_key(USER)
    assert bot._user_provider(USER) == llm_client.LLM_PROVIDER


# --- 2. a de-configured provider must fall back -----------------------------

def test_deconfigured_provider_falls_back_to_the_default(monkeypatch):
    """Membership in PROVIDERS is not the same as being usable. Without this,
    a user whose vendor lost its env keeps resolving to it forever and every
    session, rollup and /insights call fails with a generic connection error."""
    memory.save_user_profile(USER, {"llm_provider": "nanogpt"})
    monkeypatch.setattr(llm_client, "available_providers", lambda: [llm_client.LLM_PROVIDER])
    assert bot._user_provider(USER) == llm_client.LLM_PROVIDER


def test_configured_provider_is_still_honoured(monkeypatch):
    memory.save_user_profile(USER, {"llm_provider": "nanogpt"})
    monkeypatch.setattr(llm_client, "available_providers",
                        lambda: [llm_client.LLM_PROVIDER, "nanogpt"])
    assert bot._user_provider(USER) == "nanogpt"


def test_provider_command_names_a_silently_overridden_choice(monkeypatch):
    """_user_provider falls back without overwriting the profile, so the vendor
    actually serving the user can differ from the one they picked. Serving a
    different vendor than the user chose, silently, is the surprise this
    disclosure exists to prevent."""
    memory.save_user_profile(USER, {"onboarding_complete": True, "llm_provider": "nanogpt"})
    monkeypatch.setattr(llm_client, "available_providers", lambda: [llm_client.LLM_PROVIDER])
    upd = _msg_update()
    asyncio.run(bot.provider_command(upd, Mock()))
    text = upd.message.reply_text.await_args.args[0]
    assert "NanoGPT" in text and "nicht konfiguriert" in text


def test_provider_command_stays_quiet_when_the_choice_is_honoured(monkeypatch):
    memory.save_user_profile(USER, {"onboarding_complete": True, "llm_provider": "nanogpt"})
    monkeypatch.setattr(llm_client, "available_providers",
                        lambda: [llm_client.LLM_PROVIDER, "nanogpt"])
    upd = _msg_update()
    asyncio.run(bot.provider_command(upd, Mock()))
    assert "nicht konfiguriert" not in upd.message.reply_text.await_args.args[0]


# --- 3. the budget cap must not charge free providers -----------------------

def test_budget_cap_does_not_block_an_unpriced_provider(monkeypatch):
    """One process-global total, fed only by priced models but charged against
    by every provider: user A's Bedrock spend refused user B's zero-cost Groq
    call, and told B their provider's budget was exhausted."""
    calls = []
    monkeypatch.setattr(llm_client, "_daily_spend_usd", llm_client.DAILY_BUDGET_USD)
    monkeypatch.setattr(llm_client, "_create_and_track",
                        lambda m, provider=None, **k: calls.append(provider) or "ok")
    monkeypatch.setattr(llm_client, "model_for", lambda p=None: "llama-3.3-70b-versatile")
    llm_client._chat_completion([{"role": "user", "content": "hi"}], provider="groq")
    assert calls == ["groq"], "a call that cannot spend must not be refused"


def test_budget_cap_still_blocks_a_priced_provider(monkeypatch):
    """The other direction — the guard must not have been defanged."""
    monkeypatch.setattr(llm_client, "_daily_spend_usd", llm_client.DAILY_BUDGET_USD)
    monkeypatch.setattr(llm_client, "model_for",
                        lambda p=None: "mistral.mistral-large-3-675b-instruct")
    monkeypatch.setattr(llm_client, "_create_and_track",
                        lambda m, provider=None, **k: pytest.fail("must not spend"))
    with pytest.raises(llm_client.DailyBudgetExceededError):
        llm_client._chat_completion([{"role": "user", "content": "hi"}], provider="bedrock")


def test_spend_counter_survives_concurrent_threads(monkeypatch):
    """`x += y` on a module global is a read-modify-write; a GIL switch between
    load and store drops a call's cost, and an under-counted total defeats the
    cap. Two chats' turns now genuinely run in parallel to_thread workers."""
    monkeypatch.setattr(llm_client, "_daily_spend_usd", 0.0)
    monkeypatch.setattr(llm_client, "MODEL_PRICING_USD_PER_M", {"m": (1.0, 1.0)})
    usage = Mock(prompt_tokens=1_000_000, completion_tokens=0)
    resp = Mock(usage=usage)

    def hammer():
        for _ in range(200):
            llm_client._record_usage_cost(resp, "m")

    threads = [threading.Thread(target=hammer) for _ in range(4)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert llm_client._daily_spend_usd == pytest.approx(800.0), \
        f"lost updates: {llm_client._daily_spend_usd} of expected 800.0"


# --- 4. /cancel must actually stick -----------------------------------------

def test_cancel_takes_the_chat_lock(monkeypatch):
    """Without the lock, /cancel ran concurrently with an in-flight LLM turn:
    it wiped the session, then the turn resumed, re-appended to the emptied
    history it still referenced, and _save_session_state recreated the very
    active_session row the cancel had deleted — which _restore_all_users then
    brought back as an active session on the next unlock."""
    memory.save_user_profile(USER, {"onboarding_complete": True})
    bot.active_sessions[USER] = {
        "active": True, "history": [{"role": "user", "content": "x"}],
        "inactivity_jobs": [], "ctx": {}, "pending_summary": None,
    }
    order = []

    async def scenario():
        lock = bot._chat_lock(USER)

        async def in_flight_turn():
            async with lock:                       # what _process_text holds
                order.append("turn:start")
                await asyncio.sleep(0.05)
                order.append("turn:end")

        turn = asyncio.create_task(in_flight_turn())
        await asyncio.sleep(0.01)                  # let the turn take the lock
        await bot.cancel_command(_msg_update(), Mock())
        order.append("cancel:done")
        await turn

    asyncio.run(scenario())
    assert order == ["turn:start", "turn:end", "cancel:done"], \
        "the cancel must wait for the in-flight turn, not interleave with it"


def test_cancel_acknowledges_before_waiting_for_the_lock():
    """The person typing /cancel is done waiting — they must not sit through a
    90 s LLM turn with no acknowledgement. Asserted on BEHAVIOUR (a reply is
    awaited while the lock is still held elsewhere), not on source order: the
    first version compared src.index(...) and would have passed on any code
    that merely mentioned the two names in that order."""
    memory.save_user_profile(USER, {"onboarding_complete": True})
    bot.active_sessions[USER] = {
        "active": True, "history": [], "inactivity_jobs": [], "ctx": {},
        "pending_summary": None,
    }
    upd = _msg_update()

    async def scenario():
        async with bot._chat_lock(USER):          # an LLM turn is mid-flight
            task = asyncio.create_task(bot.cancel_command(upd, Mock()))
            await asyncio.sleep(0.05)             # cancel now blocks on the lock
            acked = upd.message.reply_text.await_count
        await task
        return acked

    assert asyncio.run(scenario()) >= 1, \
        "the user got no acknowledgement while /cancel waited for the lock"


# --- 5. voice messages must not overtake each other -------------------------

def test_voice_handler_serializes_transcription_under_the_lock():
    """Download and Whisper are real awaits, and before the fix they ran BEFORE
    the lock — so a short clip finished first and took the lock ahead of a
    longer one that arrived earlier, reversing history, the rolling summary and
    the stored transcript. Asserted on BEHAVIOUR: while the lock is held, the
    download must not have started."""
    import contextlib
    memory.save_user_profile(USER, {"onboarding_complete": True})
    ctx = Mock()
    ctx.bot = AsyncMock()
    upd = _msg_update()
    upd.message.voice = Mock(file_id="f", duration=3)
    upd.message.audio = None

    async def scenario():
        async with bot._chat_lock(USER):
            task = asyncio.create_task(bot.voice_handler(upd, ctx))
            await asyncio.sleep(0.05)
            started_while_locked = ctx.bot.get_file.await_count
        task.cancel()
        # CancelledError is a BaseException, so suppress(Exception) does not
        # catch it — it has to be named explicitly.
        with contextlib.suppress(asyncio.CancelledError):
            await task
        return started_while_locked

    assert asyncio.run(scenario()) == 0, \
        "download/transcription started before the chat lock was acquired"


# --- 6. one rule for session provenance -------------------------------------

def test_formulation_uses_the_session_model_not_the_current_profile(monkeypatch):
    """The row records ctx["model"], so the formulation rewrite must use the
    same value. Two rules in one function meant a mid-session /model switch
    rewrote the case formulation in the NEW model while the row claimed the
    old one."""
    memory.save_user_profile(USER, {"onboarding_complete": True, "therapy_model": "dream"})
    captured = {}

    def fake_update_formulation(summary, old, profile_ctx, model_id, provider=None):
        captured["model_id"] = model_id
        return "F"

    monkeypatch.setattr(llm_client, "update_formulation", fake_update_formulation)
    bot.active_sessions[USER] = {
        "active": True, "history": [{"role": "user", "content": "x"}],
        "inactivity_jobs": [], "ctx": {"model": "cbt", "provider": llm_client.LLM_PROVIDER},
    }
    asyncio.run(bot._finalize_session(USER, {"main_topic": "T"}))
    row = memory.get_last_n_sessions(USER, 1)[0]
    assert row["therapy_model"] == "cbt"
    assert captured["model_id"] == "cbt", \
        f"row says cbt but the formulation was rewritten as {captured['model_id']}"


# ===========================================================================
# Second review pass, 2026-09-05 — the privacy dimension.
# ===========================================================================

def test_clearing_the_name_takes_effect_in_the_running_session():
    """ctx["profile"] is snapshotted at launch and resent every turn, so
    clearing the name updated the profile while `Name: <old>` kept going out —
    and the confirmation said the opposite. The alias is what
    docs/privacy-compliance.md §8 offers as the mitigation for routing to a
    vendor whose DPA pass is open, so the claim has to be true when made."""
    memory.save_user_profile(USER, {
        "name": "Alex", "onboarding_complete": True,
        "main_concerns": "Stress", "therapy_goals": "Ruhe",
    })
    bot.active_sessions[USER] = {
        "active": True, "history": [], "inactivity_jobs": [],
        "settings_pending": "name", "onboarding_step": -1,
        "ctx": {"profile": memory.get_profile_context(USER)},
    }
    assert "Alex" in bot.active_sessions[USER]["ctx"]["profile"]
    asyncio.run(bot._process_text_locked(_msg_update(), Mock(), USER, "-"))
    assert "Alex" not in bot.active_sessions[USER]["ctx"]["profile"], \
        "the old name is still being sent to the LLM on every remaining turn"


def test_clearing_the_name_mid_session_discloses_the_history_limit():
    """Earlier assistant turns still address the user by name and the history is
    resent each turn. Rewriting a therapy transcript to erase it would falsify
    the record, so the reply must say so rather than overclaim."""
    memory.save_user_profile(USER, {"name": "Alex", "onboarding_complete": True})
    bot.active_sessions[USER] = {
        "active": True, "history": [], "inactivity_jobs": [],
        "settings_pending": "name", "onboarding_step": -1, "ctx": {"profile": "x"},
    }
    upd = _msg_update()
    asyncio.run(bot._process_text_locked(upd, Mock(), USER, "-"))
    assert "Verlauf" in upd.message.reply_text.await_args.args[0]


def test_editing_concerns_also_refreshes_the_session_context():
    """Same cached-ctx staleness — concerns and goals live in the same block."""
    memory.save_user_profile(USER, {"onboarding_complete": True, "main_concerns": "alt"})
    bot.active_sessions[USER] = {
        "active": True, "history": [], "inactivity_jobs": [],
        "settings_pending": "main_concerns", "onboarding_step": -1,
        "ctx": {"profile": memory.get_profile_context(USER)},
    }
    asyncio.run(bot._process_text_locked(_msg_update(), Mock(), USER, "neu"))
    assert "neu" in bot.active_sessions[USER]["ctx"]["profile"]


def test_privacy_names_the_provider_the_open_session_actually_uses(monkeypatch):
    """A /provider switch applies from the NEXT session. Naming the profile's
    new choice would tell someone who just switched away from a vendor that
    they had left it, while the open session still routes there."""
    monkeypatch.setattr(llm_client, "available_providers",
                        lambda: [llm_client.LLM_PROVIDER, "nanogpt"])
    memory.save_user_profile(USER, {"onboarding_complete": True,
                                    "llm_provider": llm_client.LLM_PROVIDER})
    bot.active_sessions[USER] = {
        "active": True, "history": [], "inactivity_jobs": [],
        "ctx": {"provider": "nanogpt"},
    }
    upd = _msg_update()
    asyncio.run(bot.privacy_command(upd, Mock()))
    text = upd.message.reply_text.await_args.args[0]
    assert "NanoGPT" in text, "the notice must name the vendor still carrying the data"
    assert "nächsten Sitzung" in text


def test_eval_cli_never_accepts_the_key_as_an_argument(capsys):
    """docs/server-deploy-recipe.md §4 states the rule for the far less
    sensitive bot token: it "never appears in ... the process command line".
    /proc/<pid>/cmdline is world-readable to other local accounts.
    Asserted by invoking it — argparse must REJECT the flag, not ignore it."""
    import evals.report_session_quality as rep
    with pytest.raises(SystemExit):
        rep.main(["--user-id", "1", "--key", "some-fernet-key"])
    assert "--key" in capsys.readouterr().err


def test_eval_cli_does_not_open_the_live_database():
    """memory._db() migrates and can VACUUM, so pointing it at production while
    the bot holds the WAL open risks a lock error inside _finalize_session —
    whose except branch only logs, i.e. a completed session silently lost."""
    import evals.report_session_quality as rep
    import inspect
    src = inspect.getsource(rep.main)
    assert "copytree" in src and "memory.DATA_DIR = tmp_root" in src
