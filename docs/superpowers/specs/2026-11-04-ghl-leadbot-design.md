# RiseRidge Lead Bot, part A: GoHighLevel setup (design)

Date: 2026-11-04
Source SOP: "Riseridge Lead Bot → GoHighLevel: Setup SOP" (claude.ai/artifact/7dSLx6g8TDkA6NKnE3RrWB), Step 5 and the Week 1 rollout.
Status: approved in chat 2026-11-04, awaiting spec review.

## 1. Goal and scope

Build everything GoHighLevel needs so the lead bot (part B, later) can push scored leads in and reps can work them. "Done" means: 20 test leads pushed by script land correctly (fields, tags, opportunity, owner, call task, notification), duplicates and Cold leads are handled, and all test data is removed afterwards.

In scope (part A):
- Custom fields, tags and the "Bot Leads – SEO" pipeline, created by an idempotent script.
- A Python push client that part B reuses.
- Two workflows built in the GHL web app: Route and Outcome.
- Test leads and cleanup.

Out of scope (later parts):
- Part B: scraping, enrichment, LLM scoring.
- Part C: call scripts, per-signal email drips, audit email. Part A leaves marked placeholder steps for them.
- Part D: niche retention analysis.
- The 08:00 daily report (moved to a script in a later part).

Decisions taken in chat 2026-11-04:
- Market: US only (TCPA/DNC, CAN-SPAM rules from the SOP apply to later parts).
- Location: the live RiseRidge sub-account, isolated by naming and filters. No sandbox sub-account.
- Access: GHL web app in Claude's built-in browser (user signs in) plus a Private Integration token in `.env`.
- Ingestion: API v2 (SOP Option B), not the Inbound Webhook trigger.
- Reps: already users in the sub-account; the user maps each rep to niches during the build.

## 2. Isolation rules (live sub-account)

Nothing in part A may change existing funnel objects.
- Custom fields go in a new folder "Lead Bot" (if the API cannot set the folder, fields are moved into it in the UI).
- Every tag the bot writes starts with `bot-`, `tier-`, `sig-` or `niche-`. The scripts never remove a tag they did not add, except `bot-lead` in the duplicate case.
- The pipeline is new: "Bot Leads – SEO".
- Workflow names start with `[Lead Bot]`, and every trigger is limited to that pipeline or to the `bot-lead` tag.
- Test contacts carry `bot-test` and use 555-01xx phone numbers and `@example.com` emails. Cleanup deletes only contacts with `bot-test`.

## 3. Components

```
riseridge-leadbot/
  .env                    GHL_TOKEN, GHL_LOCATION_ID (git-ignored)
  config/ghl_schema.yaml  fields, options, tags, pipeline stages, signal mapping
  leadbot/ghl/client.py   HTTP client: auth, Version header, retries, rate limits
  leadbot/ghl/setup.py    idempotent create of fields, tags, pipeline
  leadbot/ghl/push.py     push_lead(payload) -> PushResult
  leadbot/ghl/testdata.py the 20 test leads
  scripts/setup_ghl.py    CLI: run setup, print what was created vs existed
  scripts/push_test_leads.py
  scripts/cleanup_test_leads.py
  docs/ghl-build-sheet.md the workflow build, step by step, as built
  tests/                  pytest, HTTP mocked
```

Each unit has one job:
- `client.py` knows HTTP and nothing about leads. It sends `Authorization: Bearer`, `Version: 2021-07-28`, retries 429 and 5xx with backoff (honouring `Retry-After`), and never logs the token.
- `setup.py` reads `ghl_schema.yaml`, lists what exists, creates only what is missing, and reports drift (for example a field that exists with a different type) without changing it.
- `push.py` turns one bot payload (SOP Step 6 JSON) into GHL calls. It is the only module part B calls.

## 4. Data model

### 4.1 Custom fields (folder "Lead Bot", contact object)

| Field | Type | Notes |
| --- | --- | --- |
| Lead Score | Number | 0-100 |
| Lead Tier | Dropdown | Hot / Warm / Cold |
| Niche | Dropdown | starts with the SOP's 6 niches; edited after part D |
| Target City | Text | |
| Primary Signal | Dropdown | New Biz / GBP Suspended / Broken Site / Ad Waste / Rank Gap |
| All Signals | Multi-select | same 5 options |
| GBP URL | Text | |
| GBP Status | Dropdown | Verified / Unverified / Suspended suspected |
| Review Count | Number | |
| Rating | Number | one decimal |
| Est. Google Ads Spend | Monetary | USD per month |
| Meta Ads Active | Checkbox | single option "Yes" |
| Money Keyword | Text | |
| Current Rank | Number | empty when not ranking |
| Audit Summary | Large text | |
| Call Opener | Large text | |
| Bot Run ID | Text | for dedupe and QA |
| Call Outcome | Dropdown | No answer / Callback / Interested / Not interested / Closed; rep-owned |

Niche options (initial): Counselors / therapists, Roofing, HVAC, Electricians, Personal injury lawyers, Med spas / dental.

The exact API `dataType` value for each type is confirmed against the official API docs before `setup.py` is written.

### 4.2 Signal mapping

The bot's eight signal flags map to the five script categories:

| Bot signal | Primary Signal option | Tag |
| --- | --- | --- |
| new_business | New Biz | `sig-new` |
| gbp_unverified_or_suspended | GBP Suspended | `sig-gbp` |
| broken_website | Broken Site | `sig-site` |
| google_ads_spend, meta_ads_active | Ad Waste | `sig-ads` |
| rank_gap, technical_debt, competitor_gap | Rank Gap | `sig-rank` |

### 4.3 Tags

`bot-lead`, `bot-dupe`, `bot-test`, `bot-nurture-90`, `bot-try-1`, `bot-try-2`, `bot-try-3`, `tier-hot`, `tier-warm`, `tier-cold`, `niche-<slug>` (one per niche option, for example `niche-hvac`), and the five `sig-` tags above.

### 4.4 Pipeline "Bot Leads – SEO"

Stages in order: New, Contacted, Conversation, Audit Sent, Proposal, Won, Lost, Nurture. When a lead moves to Won or Lost, the opportunity status is also set to won or lost so GHL reporting counts it.

## 5. Push flow (`push_lead`)

1. Validate the payload against the Step 6 schema. If a required key is missing (business_name, and phone or email), reject and do not call GHL.
2. Look up the contact by phone, then email.
3. Duplicate guard. Mark as duplicate when either is true:
   - the existing contact is a client: it has any opportunity with status won (any pipeline), or any opportunity (any status) in the "02. GSM Appointment New Client" pipeline. A client tag from `[duplicate] client_tags` also counts, but none is set in this sub-account. The opportunity rules read one `status=all` search;
   - the contact has an opportunity with status lost changed in the last 90 days.

   On a duplicate, add `bot-dupe`, do not add `bot-lead`, create no opportunity, and return `duplicate`.
4. Upsert the contact with the fields from 4.1 and the tags `bot-lead`, `tier-*`, `niche-*`, `sig-*`. Never write Call Outcome, and never clear a field the payload leaves empty.
5. Hot or Warm: create an opportunity in "Bot Leads – SEO", stage New, named `<business_name> – <niche>`, unless an open opportunity for this contact already exists in that pipeline (then return `existing`).
6. Cold: no opportunity. The `tier-cold` tag is the hook for the part C nurture.
7. Return a `PushResult` with the contact id, opportunity id or none, and the outcome: `created`, `updated`, `existing`, `duplicate` or `rejected`.

## 6. Workflows (built in the web app)

GHL's public API does not appear to support creating workflows; this is checked before the build. If it does, the browser plan stays anyway, since workflows are a one-off build.

**[Lead Bot] Route**
- Trigger: Opportunity Created, pipeline "Bot Leads – SEO". Starting from the opportunity keeps the opportunity in the workflow's context (see the 2026-11-03 finding: a contact-triggered workflow read an empty pipeline stage).
- If/Else on Niche, one branch per niche. Each branch has an Assign To User step with that niche's reps in round robin, set to skip unavailable users if the action offers that.
- The opportunity owner is set to the same rep. The action for this is checked during the build.
- Add Task "Call: {{Primary Signal}}". Due 10:00 the next business day for Hot, within 48 hours for Warm.
- Internal notification to the assigned rep with Business, Tier, Primary Signal and Call Opener.

**[Lead Bot] Outcome**
- Trigger: Contact Changed, field Call Outcome, limited to contacts tagged `bot-lead`.
- No answer: if the contact has `bot-try-3`, this was the 4th attempt, so move to Nurture with no retry. Otherwise add the next `bot-try-N` tag and create a retry task in 2 days. Drip email 1 is a placeholder (part C).
- Callback: task at the time the rep picks, or the next business day if none is given.
- Interested: move to Audit Sent. The audit email is a placeholder (part C).
- Not interested: move to Nurture and add `bot-nurture-90`. The 90-day nurture is a placeholder (part C).
- Closed: move to Won and set status won. The onboarding trigger is a placeholder.

Every placeholder is a disabled step or a note named `TODO part C: …` so it is visible in the builder. `docs/ghl-build-sheet.md` records each workflow as built, including any setting that differs from this section.

## 7. Error handling

- 401/403: stop at once and name the missing scope. The token is never printed.
- 422 or other validation errors: log the payload's `bot_run_id` and the GHL message, skip that lead, and continue the batch.
- 429/5xx: back off and retry up to 5 times, then fail the lead.
- `setup.py` never deletes or retypes anything. Drift is reported for the user to decide.
- Partial failure (contact written, opportunity not): the result says so, and a re-push finishes the job because steps 4 and 5 are idempotent.

## 8. Testing

- Unit tests (pytest, HTTP mocked): signal mapping, payload validation, duplicate guard cases, the upsert body (Call Outcome never sent), opportunity skip for Cold and existing, retry logic, setup idempotency (second run creates nothing).
- Live run, 20 test leads: 6 Hot (one per niche), 6 Warm, 4 Cold, 2 duplicates of existing test contacts, 1 with only an email, 1 rejected (no phone or email). Checked in GHL: fields, tags, opportunity stage and owner, task and due time, notification, no opportunity for Cold, `bot-dupe` on duplicates, and round robin spreading leads across a niche's reps.
- Outcome workflow: set each Call Outcome value on one test contact and check the stage move and task.
- Cleanup: delete every `bot-test` contact and its opportunities, then confirm a search for `bot-test` returns zero. Pipeline, fields and tags stay.

## 9. Prerequisites from the user

1. Sign in to GHL in Claude's browser pane, in the RiseRidge sub-account.
2. Create a Private Integration token. Claude gives the exact scope list (checked against the docs) before this step. Put it and the location id in `.env`.
3. Map each rep user to the niches they cover.

## 10. Open items checked during the build

- API `dataType` names for each field type, and whether the custom-field API accepts a folder.
- Private Integration scope names for contacts, opportunities, pipelines, custom fields and tags.
- What marks a client in this sub-account (for the duplicate guard).
- Whether Assign To User can skip unavailable reps, and how the opportunity owner is set.
- How the Add Task action expresses "10:00 next business day".
- GHL API v2 rate limits.
