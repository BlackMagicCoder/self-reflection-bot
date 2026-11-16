# Privacy & regulatory reading pass (item 16)

> **Disclaimer:** this is the author's personal reading of the cited sources, written to inform her own design decisions for a private, non-commercial installation. It is **not legal advice**, has **not been reviewed by a lawyer**, and reflects the sources and the code as of the dates given below (latest update 2026-08-18). Provider terms and regulatory guidance change; verify everything against the current primary sources before relying on it.

Status as researched 2026-07-16; **§5 addendum from a 2026-08-04 codebase
re-review** (Bedrock provider seam, transcripts, off-site backups); **§6
(metadata-plaintext sign-off) and §7 (AWS Bedrock reading pass) added
2026-08-04** following the internal privacy/security review of that day. This covers
the two open parts of item 16: the **DPA with Groq** and an **EU AI Act reading
pass** for emotional-wellbeing chatbots. Summary up front:

> **In my reading, for a private, non-professional installation with a closed
> `ALLOWED_USERS` list, nothing further appears to be legally required.** The
> Groq DPA appears to be auto-incorporated — there seems to be nothing to sign.
> The EU AI Act and GDPR obligations would, as I read them, only come into play
> when the bot is offered to strangers; the checklist for that case is further
> below.

---

## 1. DPA with Groq — appears to be incorporated by reference

- According to its terms, Groq's [Data Processing Addendum](https://console.groq.com/docs/legal/customer-data-processing-addendum)
  is **incorporated by reference into the GroqCloud Services Agreement** for all
  cloud customers — no separate signature or execution needed.
- It covers the GDPR Art. 28 processor duties: processing only on documented
  instructions, confidentiality, security measures, breach notification, data
  subject assistance, and subprocessor management (current list at
  trust.groq.com/subprocessors, 15-day change notice).
- **EU Standard Contractual Clauses are included** (Module 2
  controller-to-processor; parties are "deemed to have signed" them on entering
  the agreement). That is the transfer mechanism for the EU→US data flow —
  Groq's retained data lives in [GCP buckets in the United States](https://console.groq.com/docs/your-data).
- Retention ([source](https://console.groq.com/docs/your-data)): **inference
  requests are not retained by default**; data may be kept up to 30 days only
  for reliability troubleshooting / abuse investigation. A **zero-data-retention
  (ZDR) setting** is available to all customers in the console (Data Controls)
  and disables even that.

**Recommended (one-time, console):** enable ZDR in the GroqCloud console — this
bot uses neither batch nor fine-tuning endpoints, so nothing is lost.

## 2. EU AI Act

- **Private personal use appears to be out of scope as deployer**:
  [Art. 2(10)](https://artificialintelligenceact.eu/article/2/) exempts natural
  persons using AI "in the course of a purely personal non-professional
  activity". In my reading, a self-built bot used privately, without income and
  with a closed `ALLOWED_USERS` list, falls under this. The exemption is construed narrowly — it ends
  as soon as any regular economic benefit or occupational context appears.
- **If the bot is ever offered to others**, the operator likely becomes a
  *provider* of an AI system, and
  [Art. 50(1)](https://artificialintelligenceact.eu/article/50/) applies
  (**from 2 August 2026**): users must be informed they are interacting with an
  AI system, at the latest at first interaction, unless obvious. The privacy
  notice shown before onboarding now states this explicitly (see `_privacy_text()`
  in `bot.py`), which in my reading satisfies the disclosure timing — the notice gates the
  first conversation.
- **Not high-risk, not prohibited**, as I read the text today:
  - Wellbeing/self-reflection chatbots are not an Annex III high-risk category.
  - The Art. 5 emotion-recognition prohibition targets inference of emotions
    from **biometric data** in workplace/education contexts. This bot processes
    self-reported text; voice messages are transcribed to text (Whisper) and the
    audio is not stored or used for identification — not biometric
    categorization.
  - Art. 5 manipulation prohibitions target techniques that materially distort
    behavior causing significant harm; a consented, supportive self-reflection companion
    does not appear to fit.
- **Medical-device boundary (MDR, not AI Act)**: the bot must keep *not*
  claiming to diagnose or treat any condition. The system prompt forbids
  diagnosis and the privacy notice + README state it is no substitute for
  therapy. Keep that stance — a claimed medical purpose would change the
  classification fundamentally.

## 3. GDPR beyond the DPA

- Purely personal/household use appears to fall under the GDPR household
  exemption (Art. 2(2)(c)) — in my reading, a private installation of this kind
  is not a "controller" situation.
- The bot nevertheless already implements the controller duties that would
  apply later: consent gate before onboarding, `/privacy` notice naming Groq
  and Telegram as recipients, `/export` (Art. 15/20), `/delete` including all
  backups (Art. 17), Fernet encryption at rest with per-user keys,
  `DATA_RETENTION_DAYS` auto-deletion.

## 4. Checklist before opening registration to strangers

1. Enable **ZDR** in the Groq console (advisable in any case).
2. Re-read the then-current Groq DPA/subprocessor list; keep a dated copy.
3. Extend the privacy notice: name the session content explicitly as **health
   data (GDPR Art. 9)** and make consent wording match ("ausdrückliche
   Einwilligung"); mention the EU→US transfer under SCCs.
4. Confirm the Art. 50 AI-disclosure line still shows before first interaction
   for every entry path.
5. Start a minimal record of processing activities (Art. 30) — one page.
6. Re-check Annex III / Commission guidance for new categories covering
   wellbeing chatbots (regulatory landscape still moving).
7. Decide the crisis-detection question consciously at that point: the current
   design decision (no crisis detection) is documented in `docs/architecture.md`; opening
   to strangers changes the risk calculus and should trigger a deliberate
   re-decision, not a silent default. (This is a product/ethics
   gate, not a legal obligation identified in this pass.)
8. *(added 2026-08-04)* Redo the §1 DPA/retention reading for whichever
   `LLM_PROVIDER` is actually live at that point (Groq: §1, researched
   2026-07-16; AWS Bedrock: §7, researched 2026-08-04 — re-verify both if
   stale by then), and name the off-site backup provider in the notice if
   `BACKUP_REMOTE` is set.

## 5. Addendum — codebase re-review 2026-08-04

The bot changed materially after the 2026-07-16 pass above. What that means for
this document, in decreasing order of weight:

1. **Second chat provider (`LLM_PROVIDER=bedrock`, available since
   2026-08-01).** Everything in §1 (DPA, retention, ZDR, SCCs) was researched
   for **Groq only**. With this setting the bot sends chat traffic to AWS Bedrock
   (Mistral Large 3); an equivalent AWS data-processing reading pass had NOT
   been done at the time of this addendum. The `/privacy` notice names the
   correct vendor since 2026-08-01, so the *disclosure* was right — the *legal
   review* behind it was missing. **Done 2026-08-04 — see §7 below** (security
   review finding PRIV-004).
2. **Voice messages always go to Groq, regardless of `LLM_PROVIDER`.**
   Transcription is Groq Whisper (`llm_client._audio_client`), so under
   `LLM_PROVIDER=bedrock` Groq remains a data recipient for voice audio — but
   the `/privacy` vendor line then named only AWS Bedrock. **Fixed
   2026-08-04**: both vendor branches of `_privacy_text()` now disclose the
   voice→Groq flow (tested in `tests/test_bot_helpers.py`).
3. **The stored-data inventory grew after the notice was written.** Since the
   notice's list was drafted, the bot additionally stores: **full verbatim
   session transcripts** (`sessions.transcript`), Week/Month/Year rollups
   (`period_summaries`), and the persisted active-session state including its
   full history (`kv('active_session')`). All of it is Fernet-encrypted like
   everything else and covered by `/export` and `/delete` — but the notice's
   "Was gespeichert wird" list still said "Sitzungszusammenfassungen" without
   mentioning that complete transcripts are kept. **Fixed 2026-08-04**: the
   list now names transcripts, rollups, and the persisted active session.
4. **Off-site backups (`BACKUP_REMOTE`, opt-in).** The nightly `rclone sync` of `backups/` would place Fernet
   ciphertext with a third-party storage provider. Ciphertext of personal data
   is still personal data under GDPR (the keys exist, just not there) — in my
   reading acceptable under the household exemption, but the provider must be named in the
   notice before opening to strangers (checklist item 8). Timing caveat:
   `/delete` purges local data and local backups immediately, but an
   already-synced off-site copy only disappears with the next successful
   nightly sync (documented in README and `docs/architecture.md`).
5. **`DATA_RETENTION_DAYS` only ever fires for unlocked users.** The retention
   check lives in `bot._restore_all_users`, which skips locked users (it needs
   the user's own key to read their last-session date). Auto-deletion is
   therefore best-effort: a user who never unlocks again is never auto-deleted.
   Acceptable for private use; relevant to any future storage-limitation
   (Art. 5(1)(e)) claim.
6. **`/bug` reports** forward the user's next message verbatim to
   `OWNER_CHAT_ID` — a deliberate, user-initiated data flow (stays within
   Telegram, no session content attached). No action needed; recorded here so
   the data-flow inventory is complete.

## 6. Metadata-plaintext posture — conscious sign-off (2026-08-04, PRIV-015)

The internal security review of 2026-08-04 (finding PRIV-015) asked
for an explicit decision on the columns that are **deliberately not
Fernet-encrypted** inside the otherwise value-encrypted `user.db`:

| Plaintext column(s) | Why plaintext |
|---|---|
| `mood_log.date`, `notes.date`, `emotions.date` | Range queries (`memory.get_*_in_range`) filter on them in SQL; Fernet ciphertext is non-deterministic and unorderable |
| `period_summaries.period_type` / `period_key` | Back the `UNIQUE(period_type, period_key)` idempotency constraint — non-deterministic ciphertext cannot back a UNIQUE constraint |
| Schema, table names, row ids, row counts | Inherent to value-level (not file-level) encryption |

**What this leaks to someone with file access but no key:** an activity
calendar — *when* and *how often* the user journaled, logged moods/emotions,
and held sessions — but no content. (`mood_log.score` was part of this leak
until 2026-08-04; it is now encrypted — security review finding PRIV-002.)

**Decision: accepted as-is for a private installation.** Rationale: the
behavioral pattern is meaningful but far less sensitive than content; the
dates are load-bearing for range queries and the rollup idempotency
constraint; and the parties this design distrusts (storage host, backup
remote) gain no content access from it. The alternative — encrypt the dates
and switch every range query to decrypt-then-filter — is viable at this
scale and remains the documented fallback. **Revisit trigger:** before opening
the bot to further users, or before `BACKUP_REMOTE` places `user.db` copies with an
off-site provider at scale, re-decide consciously (add it to the §4
checklist walk-through at that point).

## 7. AWS Bedrock data-processing reading pass (2026-08-04, PRIV-004)

Researched 2026-08-04 for this configuration: OpenAI-compatible
endpoint `https://bedrock-mantle.us-east-1.api.aws/v1` ("Mantle"), Chat
Completions API, Bedrock API key (bearer token), model
`mistral.mistral-large-3-675b-instruct` (serverless third-party model). All
source links retrieved 2026-08-04; primary sources are AWS's own docs.

1. **No training on customer content.** AWS states customer content is not
   used to train or improve any models, AWS's own or third-party providers':
   *"your content is not used to improve the base models and is not shared
   with any model providers"*
   ([Bedrock FAQ](https://aws.amazon.com/bedrock/faqs/), [data protection
   docs](https://docs.aws.amazon.com/bedrock/latest/userguide/data-protection.html)).
   Explicit. (The corresponding AWS Service Terms clause — §50.12 per
   search-indexed excerpts — was not verified against the primary source;
   check it in a browser. Terms page version at retrieval: "Last updated:
   July 29, 2026".)
2. **No storage/logging of prompts or completions by default.** Bedrock
   documents a zero-operator-access + zero-data-retention default: *"by
   default, Amazon Bedrock does not store model inputs or outputs"*
   ([abuse-detection docs](https://docs.aws.amazon.com/bedrock/latest/userguide/abuse-detection.html)).
   Carve-outs checked: abuse-detection retention applies only to named models
   (OpenAI GPT-5.x, Anthropic Fable 5) — **Mistral Large 3 is not on that
   list**; CSAM hash-matching applies to image inputs only (this bot sends
   none); [model invocation logging](https://docs.aws.amazon.com/bedrock/latest/userguide/model-invocation-logging.html)
   is opt-in, off by default, and *does not capture Mantle-endpoint calls at
   all*. A 2026 account-level
   [`data_retention_mode`](https://docs.aws.amazon.com/bedrock/latest/userguide/data-retention.html)
   setting (`none`) turns the default posture into a guaranteed,
   API-verifiable ZDR (Bedrock then refuses models that would require
   retention; Mistral Large 3 requires none). **Recommended one-time step
   (analogous to the Groq ZDR toggle):** set `data_retention_mode`
   to `"none"` via `PUT https://bedrock-mantle.us-east-1.api.aws/v1/data_retention`.
3. **Model provider (Mistral AI) cannot see the content.** Models run in
   AWS-operated Model Deployment Accounts: *"model providers don't have
   access ... to customer prompts and completions"*
   ([data protection docs](https://docs.aws.amazon.com/bedrock/latest/userguide/data-protection.html)).
   Explicit. (Gap noted: AWS's [third-party-models legal
   page](https://aws.amazon.com/legal/bedrock/third-party-models/) had no
   Mistral section at retrieval time — Mistral-specific EULA terms were not
   publicly determinable; the general AWS statements above stand regardless.)
4. **Region: processing stays in us-east-1 for this model.** The
   [Mistral Large 3 model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-mistral-ai-mistral-large-3.html)
   lists no geo/global inference profile at all (cross-region inference not
   supported for this model), so a bare-model-id call is by definition
   in-region; the FAQ states content is *"stored at rest in the AWS Region
   where you are using Amazon Bedrock"*. Tightly inferred + explicit. Re-check
   if the model id in `.env` ever changes or grows a `us.` prefix. EU
   trade-off recorded honestly: `bedrock-mantle` endpoints exist in EU
   regions, but Mistral Large 3 is not offered in-region there — moving the
   workload to the EEA would require a model change.
5. **GDPR layer.** The [AWS DPA and 2021 EU SCCs are auto-incorporated into
   the AWS Service Terms](https://aws.amazon.com/compliance/gdpr-center/)
   (nothing to sign — same shape as Groq's §1); AWS is
   [certified under the EU-U.S. Data Privacy Framework](https://aws.amazon.com/compliance/eu-us-data-privacy-framework/)
   (under the Amazon.com, Inc. listing), so the EU→US transfer has DPF
   adequacy with SCCs as fallback. Bedrock appears in scope for SOC 1/2/3 and
   ISO 27001/17/18/701 per search-indexed AWS content — the
   [services-in-scope page](https://aws.amazon.com/compliance/services-in-scope/)
   was not verified against the primary source; check it in a browser.
6. **Mantle-endpoint specifics.** The endpoint is a first-party Bedrock
   endpoint in the official [User Guide](https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-mantle.html).
   One standing caveat: on Mantle, the **Responses API** (which this bot does
   NOT use) stores request+response for 30 days by default (`store=true` is
   the OpenAI-spec default) — **Chat Completions has no such stateful
   storage**. If `llm_client` ever switches to `client.responses`, that
   silently opts into 30-day storage; don't, or pass `store=false`. API keys
   are not logged in CloudTrail; a static `BEDROCK_API_KEY` is an AWS
   "long-term" key, which AWS labels *"for exploration only"* — consider
   short-term keys for anything beyond private use
   ([api-keys docs](https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys.html)).
7. **Health-data signal:** Bedrock is on the
   [HIPAA Eligible Services list](https://aws.amazon.com/compliance/hipaa-eligible-services-reference/)
   as "Amazon Bedrock [excluding Fable and Mythos models]" — the exclusions
   are exactly the retention-requiring models; Mistral Large 3 is inside the
   eligible envelope. Signal only (US regime, needs a BAA, not a GDPR
   determination).

**Summary (my reading):** on paper the Bedrock posture appears at least as strong as
the reviewed Groq posture — contractual no-training, no provider access,
default ZDR/ZOA with an API-enforceable hard mode, auto-incorporated DPA/SCCs,
DPF certification, and no cross-region routing for this model. The structural
weak point is unchanged from Groq: processing happens in the US. **Open
follow-ups:** (a) set `data_retention_mode: "none"` (see 2.);
(b) one browser pass over Service Terms §50.12 and the services-in-scope
table (primary sources not verified); (c) as with all provider facts, this reflects
AWS docs as of 2026-08-04 — re-verify before relying on it at scale. This
section does not change the Art. 9 analysis: in my reading the lawful basis
remains the bot's own explicit-consent flow, exactly as with Groq.

Sources: [Groq DPA](https://console.groq.com/docs/legal/customer-data-processing-addendum) ·
[Your Data in GroqCloud](https://console.groq.com/docs/your-data) ·
[Groq Privacy Policy](https://groq.com/privacy-policy) ·
[AI Act Art. 2](https://artificialintelligenceact.eu/article/2/) ·
[AI Act Art. 50](https://artificialintelligenceact.eu/article/50/) ·
[Art. 50 practical guide](https://artificialintelligenceact.eu/transparency-rules-article-50/)

## 8. NanoGPT — reading pass OPEN; offered on an explicit decision of the author (2026-08-18)

Status: **NanoGPT is wired into the code as a selectable provider
(`/provider`), but its data-processing review has NOT been done.** This
section exists so that state is recorded rather than implied; `bot.main()`
logs a warning at every start while NanoGPT is configured, and the `/privacy`
disclosure line for NanoGPT names it as a routing service over changing
third-party models.

What is known (second-hand only — the primary policy at `nano-gpt.com` was
not retrieved and not verified; check it in a browser; do not treat this
section as a completed review):

- NanoGPT is a **routing layer** over third-party inference providers. Their
  own wording: ZDR applies "only where the selected provider and route
  explicitly support it", and upstream providers "may still retain data under
  their own legal obligations and privacy policies". The Art. 28
  sub-processor chain therefore moves downstream rather than away.
- Claimed: no prompt/conversation storage by default, IPs not linked to
  prompt content, short-lived abuse/rate-limit logs, strictest-available
  retention settings per onboarded provider, prepaid (incl. crypto) payment
  without an account requirement.
- **Not findable via search**: a DPA, the legal entity and its jurisdiction,
  a sub-processor list, an Art. 27 EU representative.

The questions to answer before this section can be closed (ordered — stop
at the first hard no):

1. Is there a **DPA** (Art. 28) at all, signable by an individual, not just
   enterprise? Without one, stop — nothing else matters.
2. Which **legal entity**, which **jurisdiction**? Check the imprint/ToS and a
   business register.
3. Is there a **sub-processor list**, and does it name the downstream inference
   providers per model/route? Art. 28(4) requires the same obligations to flow
   down by contract.
4. **Data location** — can routing be pinned to EU/EEA, or at least to
   providers under SCCs/DPF? The equivalent question to §7's "no cross-region
   routing" is asked per route here.
5. Can ZDR be **enforced rather than hoped for** — a per-request or account
   setting that refuses non-ZDR routes? An unenforced ZDR default is not a
   control.
6. Art. 27 **EU representative**, if the entity sits outside the EU.

Until then:

- The author's decision (2026-08-18) to offer NanoGPT anyway is recorded here.
  Context that makes this defensible for a private installation with a closed
  `ALLOWED_USERS` list: every user sees the provider-specific `/privacy` text
  and chooses their provider themselves via `/provider` — this is not a
  service offered to strangers (§4's checklist would make NanoGPT a blocker
  there).
- **Mitigation shipped alongside**: the profile name became a user-editable
  alias (`/profile` → "Name/Alias ändern", "-" clears it entirely; an empty
  name sends no name line at all). That removes the one *systematic*
  identifier from every request. Honest limits, stated in the UI too: free
  text still identifies through content, and the formulation/older summaries
  can carry a previously-used name until naturally rewritten by later
  sessions. Data minimization, not anonymization — GDPR still applies in
  full (Recital 26).
- `DAILY_BUDGET_USD` does not price NanoGPT models; that is deliberate — the
  prepaid wallet is itself the spend ceiling there, unlike Bedrock's
  open-ended billing (see `llm_client._record_usage_cost`).
