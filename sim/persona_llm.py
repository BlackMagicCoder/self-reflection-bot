"""Persona LLM seam — completely separate from llm_client.py's bot-under-test
Groq client and model choice, so the bot-under-test's own usage/behavior
stays untouched by anything the simulator does.

Now on AWS Bedrock Mantle (switched 2026-07-22, closing the item
opened after P3's first attempt) — a genuinely different vendor from the
bot-under-test's Groq/Meta-Llama, fully satisfying the simulator's persona-
hygiene rule (P3's first attempt used Groq for both, mitigated only at the
model-family level).

PERSONA_MODEL defaults to mistral.mistral-large-3-675b-instruct: a
different vendor from both Meta Llama (bot-under-test) and OpenAI's
gpt-oss-20b (already validated once on Groq — see sim/smoke_persona.py's
first run). Chosen after live-testing accessibility (2026-07-22) — Claude
Haiku 4.5 (the original pick) turned out to be account-gated ("not
available for this account" — matches the reference implementation's own prior
Bedrock recon note about newest-tier Anthropic models needing an AWS
Sales conversation). DeepSeek V3.2, Qwen3-235B, and GLM-5 were also
confirmed live-accessible, but Mistral Large 3 is the cheapest of the four
($0.50/$1.50 per M tokens vs. $0.62-$1.85/$1.85-$3.20 for the others) and
Mistral's French training background gives it strong German fluency,
useful since every prompt here is German. Cost estimate for this project's
total remaining live usage (~400 short persona turns across P3-P5) is well
under $1, even with margin for retries.
Frozen + stamped into every run's summary.json (see sim/run_sim.py) so any
future swap doesn't silently change what a past run used.

API notes (all verified live 2026-07-22, not assumed from docs alone):
Bedrock Mantle exposes an OpenAI-compatible Chat Completions endpoint at
BEDROCK_ENDPOINT_URL (broadly supported — Mistral/Qwen/DeepSeek/GLM/gpt-oss
all confirmed live) and a narrower OpenAI Responses endpoint (OpenAI's own
models only — the first attempt at this used Responses and a 400 revealed
Claude doesn't support it). Claude models instead need the Anthropic
Messages API at a DIFFERENT base path (".../anthropic" vs the OpenAI-style
".../v1") — kept here as SIM_PERSONA_PROVIDER=bedrock-anthropic for if/when
Claude access is ever granted, but Chat Completions (SIM_PERSONA_PROVIDER=
bedrock, the default) is what this project actually uses.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

PERSONA_PROVIDER = os.getenv("SIM_PERSONA_PROVIDER", "bedrock")
PERSONA_MODEL = os.getenv("SIM_PERSONA_MODEL", "mistral.mistral-large-3-675b-instruct")
PERSONA_TEMPERATURE = float(os.getenv("SIM_PERSONA_TEMPERATURE", "0.7"))
PERSONA_MAX_TOKENS = int(os.getenv("SIM_PERSONA_MAX_TOKENS", "1024"))

_client = None


def _bedrock_api_key() -> str:
    return os.getenv("BEDROCK_API_KEY", "") or os.getenv("AWS_BEARER_TOKEN_BEDROCK", "")


def _anthropic_base_url() -> str:
    # BEDROCK_ENDPOINT_URL is the OpenAI-style base (".../v1"); the Anthropic
    # Messages API lives at the sibling ".../anthropic" path on the same host.
    openai_style = os.environ["BEDROCK_ENDPOINT_URL"]
    host = openai_style.rsplit("/v1", 1)[0]
    return f"{host}/anthropic"


def _get_client():
    global _client
    if _client is None:
        if PERSONA_PROVIDER == "bedrock":
            from openai import OpenAI
            # Explicit timeout: an eval run (2026-07-29, items 41/42
            # live check) hung 25+ minutes on a single completion call — an
            # established TCP connection to the Bedrock endpoint that never
            # returned a response and the SDK's default (no timeout) just
            # waited forever. Fail fast instead so a stuck call surfaces as
            # an error, not a silent multi-hour hang.
            _client = OpenAI(base_url=os.environ["BEDROCK_ENDPOINT_URL"],
                             api_key=_bedrock_api_key(), timeout=90.0)
        elif PERSONA_PROVIDER == "bedrock-anthropic":
            from anthropic import Anthropic
            _client = Anthropic(
                base_url=_anthropic_base_url(),
                api_key=_bedrock_api_key(),
                default_headers={"anthropic-version": "2023-06-01"},
                timeout=90.0,
            )
        elif PERSONA_PROVIDER == "groq":
            from groq import Groq
            _client = Groq(api_key=os.getenv("GROQ_API_KEY", ""))
        else:
            raise ValueError(f"unsupported SIM_PERSONA_PROVIDER {PERSONA_PROVIDER!r}")
    return _client


def complete(messages: list[dict], system: str = "") -> str:
    """(messages, system) -> next persona message text.

    Matches sim.persona.LLMPersona's complete_fn(messages, system=...)
    call signature exactly.
    """
    client = _get_client()

    if PERSONA_PROVIDER == "bedrock-anthropic":
        response = client.messages.create(
            model=PERSONA_MODEL, max_tokens=PERSONA_MAX_TOKENS,
            system=system or None, messages=messages,
            temperature=PERSONA_TEMPERATURE,
        )
        return "".join(b.text for b in response.content if b.type == "text")

    # "bedrock" (OpenAI-compatible Chat Completions) and "groq" both speak
    # the same Chat Completions shape.
    full_messages = ([{"role": "system", "content": system}] if system else []) + messages
    response = client.chat.completions.create(
        model=PERSONA_MODEL, messages=full_messages, temperature=PERSONA_TEMPERATURE,
        max_tokens=PERSONA_MAX_TOKENS,
    )
    return response.choices[0].message.content or ""
