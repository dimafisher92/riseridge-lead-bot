# RiseRidge Lead Bot, part B1: collect, score, qualify (design)

Date: 2026-11-05
Source SOP: "Riseridge Lead Bot → GoHighLevel: Setup SOP" (claude.ai/artifact/7dSLx6g8TDkA6NKnE3RrWB), Steps 2, 3, 4, 6 and 8.
Builds on: part A (`docs/superpowers/specs/2026-11-04-ghl-leadbot-design.md`, PR dimafisher92/riseridge-lead-bot#1).
Status: design approved in chat section by section 2026-11-05, awaiting spec review.

## 1. Goal and scope

Every night, find counselors/therapists in three Texas cities whose Google presence shows a fixable problem, score them with the SOP's point table, have Claude write the rep-facing text, and push the callable ones into GoHighLevel through part A's `push_lead()`.

"Done" means: a live dry run for one city produces a `qualified.jsonl` a human agrees with (every Hot and Warm lead's signals are backed by its evidence), and the first live push, run only on the user's go-ahead, lands leads in GHL that part A's workflows route to Sergey.

In scope (B1):
- Seeds (niche × city × queries) in config.
- Google Maps scraping with gosom's open-source scraper (patched Windows build, see 3.2).
- Website check, PageSpeed mobile score and domain age.
- Signal detection with evidence, scoring and tiering in code.
- National Do Not Call scrub against downloaded FTC area-code files.
- A nightly Claude Code scheduled task that judges chains/franchises, writes the audit summary and call opener, and runs the push.

Out of scope (later parts):
- B2: Google Ads spend (SpyFu or similar), active Meta ads, the "tiny ad budget" negative.
- B3: SearchAtlas rank and technical-audit signals.
- Part C: call scripts, email drips, audit email. Part D: niche retention analysis.

Decisions taken in chat 2026-11-05:
- Google Maps source: gosom/google-maps-scraper, run locally, no API key. Its vendor skill "google-maps-scraper" is kept as reference documentation in `docs/vendor/google-maps-scraper/` (moved out of `.claude/skills/` so the nightly task cannot load it; `skills-lock.json` at the repo root still records the install).
- Orchestration and LLM step: a Claude Code scheduled task on this PC (no Anthropic API key). Runs only while the Claude app is open; a missed night runs on the next launch.
- Approach: code detects signals and scores (deterministic, tested); Claude only judges chains/franchises and writes text.
- First run: niche Counselors / therapists; cities Austin, Dallas, Houston (TX).
- Tier thresholds stay as in the SOP (Hot 70+, Warm 45-69, Cold under 45) although B1 cannot reach the ad points.
  - 2026-11-05, after the calibrated Austin dry run, the user lowered the B1 Warm cut to 30 (Hot 70 unchanged) until B2 adds the ad signals.
- DNC: scrub in B1. The user registers with the FTC and saves the area-code files; go-live waits for them.

## 2. Pipeline

```
nightly Claude scheduled task (02:00, this PC), once per city
  1. python -m leadbot.bot collect --niche "Counselors / therapists" --city "Austin, TX"
       seeds -> query file -> scraper (1 worker, JSON) -> raw.jsonl
       -> dedupe by place_id; skip place_ids seen in the last 30 days
       -> drop: closed status, no phone, DNC match, already a GHL contact
       -> enrich (capped): website check, PageSpeed (only if another signal), domain age
       -> signals + evidence + score + tier
       -> state/runs/<run_id>/candidates.jsonl  (+ dropped.jsonl with reasons)
  2. Claude (skill .claude/skills/leadbot-nightly/SKILL.md) reads candidates.jsonl:
       Hot, Warm, and Cold with an email only
       -> drop chains/franchises with a stated reason
       -> write audit_summary and call_opener from the evidence
       -> state/runs/<run_id>/qualified.jsonl
  3. python -m leadbot.bot push state/runs/<run_id>/qualified.jsonl [--dry-run]
       -> validate_payload + push_lead() (part A, unchanged)
       -> state/runs/<run_id>/report.json + printed summary
```

`run_id` = `<YYYY-MM-DD>-<niche slug>-<city slug>`, for example `2026-11-06-counselors-therapists-austin-tx`. Each lead's `bot_run_id` = `<run_id>-<NNNN>` (row number in candidates).

## 3. Components

```
leadbot/bot/
  seeds.py       config/seeds.toml -> queries for one niche + city
  scrape.py      runs the scraper exe, reads its JSON Lines, normalises rows
  seen.py        state/seen.sqlite: place_id -> first_seen, last_seen, last_outcome
  dnc.py         loads FTC area-code files, answers "is this number listed"
  site_check.py  homepage fetch: form, tel: link, gtag/GTM/Meta Pixel, mailto email
  pagespeed.py   PageSpeed Insights mobile performance score (no key; unknown on quota error)
  domain_age.py  RDAP registration date
  signals.py     raw + enrichment -> signals with evidence, gbp_status, money keyword rank
  score.py       config/scoring.toml -> score, tier, primary signal
  candidates.py  candidate record shape, read/write JSONL, Claude-output validation
  cli.py         `collect` and `push` subcommands
config/seeds.toml, config/scoring.toml
.claude/skills/leadbot-nightly/SKILL.md   instructions for the scheduled task
```

Each module has one job and is tested without network: the scraper with a recorded JSON fixture (built from the 2026-11-05 Austin HVAC test, with names, phones and URLs replaced by fictional ones), HTTP calls with a fake transport, the exe with a fake runner.

### 3.1 Seeds (`config/seeds.toml`)

Per niche: the money keyword template and query templates. Per city: suburb or neighbourhood names. For Counselors / therapists the money keyword is `therapist {city}` and queries include `therapist in {area}`, `counseling in {area}`, `couples counseling {city}`. About 6 queries per city. The money keyword query always runs first, because its result order gives the Maps rank used by `rank_gap` and `competitor_gap`.

### 3.2 Scraper

- Binary: `C:/Users/dimaf/tools/google-maps-scraper/gmaps-patched.exe`, path set in `.env` as `GMAPS_SCRAPER_EXE`. Built 2026-11-05 from gosom/google-maps-scraper v1.18.1 with gosom/scrapemate v1.4.0, removing the `--single-process` Chrome flag (`adapters/fetchers/jshttp/jshttp.go`), which crashes Chrome on Windows (upstream issue gosom/google-maps-scraper#322, open). The official v1.18.1 Windows exe returns "unexpected page type" and 0 rows here. Rebuild steps go in `docs/scraper-build.md`.
- Flags: `-input <file> -results <file> -json -depth <n> -c 1 -lang en -exit-on-inactivity 3m`. Depth comes from seeds config (starts at 5).
- Fields used: `title, category, categories, phone, web_site, owner, status, review_count, review_rating, user_reviews[].When, place_id, cid, link, complete_address, emails, input_id`.
- The vendor skill (reference only, `docs/vendor/google-maps-scraper/`): its scripts are Docker-only and are not used. Its guidance is followed: conservative depth and concurrency, no proxy, keep partial output.

### 3.3 Filters before enrichment

In order, each drop recorded in `dropped.jsonl` with its reason:
1. `status` says closed (permanently or temporarily).
2. No phone (reps call; Cold leads without a phone are not pushed either).
3. Phone listed in the DNC files (`dnc`), or, right after, its area code has no loaded DNC number (`dnc_area_not_loaded`: fail closed).
4. place_id seen in the last 30 days. A place whose last recorded outcome is `failed` is never "seen": a live push that fails for a lead (for example after its contact was created but before its deal) records `failed` in the seen store, and the place is collected again next night.
5. Already a GHL contact (part A's `find_duplicate` by phone, then email): `ghl_contact`, unless the seen store's last outcome for the place is `failed` (our own push died midway); then the place continues and part A's `push_lead` creates the missing deal, or returns `existing`/`duplicate`. Old bot leads are never re-pushed, because only the bot's own local failure record re-admits a contact: part A's `push_lead` would update any contact that is not a client, won deal or recent loss, and B1 never re-engages an existing contact otherwise. A successful push overwrites `failed` with its outcome. A lookup that fails for any reason other than an auth error drops only that place (`ghl_lookup_failed`, not marked seen); an auth error stops the run.

Then, within the run, a later place with the same phone as a kept one is dropped (`shared_phone`), and at most 150 places per city per night go to enrichment, in Maps result order (`over_nightly_cap` for the rest). After enrichment an email found on more than one candidate is cleared on all of them, and a website `mailto:` on another registrable domain than the website's is ignored.

### 3.4 Enrichment

- **Site check:** GET the homepage with a 15 s timeout and the bot's User-Agent; follow redirects. Record: reachable, final URL, has `<form`, has `tel:` link, has gtag/`googletagmanager.com`/`fbq(`, first `mailto:` address. A refusal (HTTP 401, 403, 429 or 503, as a status or an `HTTPError`) is recorded as `blocked: true` with the status: the site may be fine, the bot was refused. Any other HTTP error status is `reachable: false` with the status; a network failure (DNS, refused connection, timeout, TLS) is `reachable: false` with no status.
- **PageSpeed:** PageSpeed Insights v5 `runPagespeed?strategy=mobile` without a key, only for places that already have another TRUE signal. Quota or error means `unknown`.
- **Domain age:** RDAP lookup of the registrable domain (`https://rdap.org/domain/<domain>`), registration event date. Error means `unknown`.

### 3.5 Signals (code)

Each signal is TRUE, FALSE or UNKNOWN, with a one-line evidence string. UNKNOWN scores 0.

| Signal | TRUE when |
| --- | --- |
| `gbp_unverified_or_suspended` | `owner` is empty. GBP Status = Unverified. "Suspended suspected" is not detectable from Maps data in B1 and is never set. |
| `broken_website` | no website; or the site did not load (evidence adds the HTTP status when known); or the page loaded and (no lead form AND no `tel:` link) OR mobile PageSpeed under 40. A missing tracking tag is listed in the evidence but never makes it TRUE. A site that refused the bot (`blocked`) is UNKNOWN. |
| `new_business` | domain registered under 6 months (183 days) ago, OR (every review is visible, i.e. the dated reviews number at least `review_count` ≥ 1, AND the oldest is at most 92 days old AND `review_count` ≤ 15). When only some reviews are visible and the domain is not recent, the signal is UNKNOWN ("only N of M reviews visible"): Maps shows a sample, and a hidden older review would make TRUE wrong. More than 15 reviews, or an oldest visible review over 92 days old, is FALSE. |
| `rank_gap` | the place's position in the money keyword query is 4 to 20 (just outside the map pack). Positions 1-3 (`MAP_PACK`) are FALSE, and 21 or worse (`NEAR_MISS_MAX` = 20) is FALSE too: a listing that far down is not a near miss. Not in the money results is UNKNOWN, evidence "not in the top N on Maps for '<keyword>'" (kept as a note on the candidate). No money results at all is UNKNOWN. |
| `competitor_gap` | only evaluated when the place is in the money results (otherwise UNKNOWN). The rivals are the first 3 money places excluding the place itself and chain listings (a brand key, the lowercased name cut at the first " - ", " | ", " – " or " (", that appears on more than one listing among all places scraped for the city in that run). No independent rival is UNKNOWN. TRUE when the rivals' average `review_count` is at least 3× this place's (minimum 1). |
| `google_ads_spend`, `meta_ads_active`, `technical_debt` | always UNKNOWN in B1. |

**Negative signal** (`negatives(place, today)`, never a GHL flag): `inactive_listing` is TRUE when every review is visible (dated reviews number at least `review_count`) and the newest is more than 730 days (`INACTIVE_DAYS`) old; FALSE when every review is visible and the newest is recent; UNKNOWN when the place has no review dates or only a sample is visible ("only N of M reviews visible"), because an unseen newer review would make TRUE wrong. It lowers the score and is not part of `signals`.

Relative review ages ("2 weeks ago", "Edited a month ago", "a year ago") are parsed to an approximate age in days; unparseable text means UNKNOWN for that review.

### 3.6 Scoring (`config/scoring.toml`)

Weights from SOP Step 4: gbp 25, google ads 20 (+10 over $3k/month), meta ads 10, broken site 15, new business 15, rank gap 10, technical debt 5, competitor gap 5, retention niche +5 (Counselors / therapists listed), negative −30 per TRUE negative (`negative_signal`, applied after the positives and the retention bonus, floor 0). Capped at 100. Tiers: Hot 70+, Warm 30-69 in B1 (SOP: 45-69; restored with B2), Cold under 30. `primary_signal` = the GHL category of the highest-weight TRUE signal (ties: table order). B1 maximum is 75.

### 3.7 Candidate record

One JSON object per line, carrying the part A payload keys (`business_name, phone, email, website, niche, city, gbp_url, gbp_status, review_count, rating, money_keyword, current_rank, signals, primary_signal, lead_score, lead_tier, bot_run_id`) plus `evidence` (signal → text; also the evidence of a TRUE negative), `unknown` (list of signals), `place_id`, `negatives` (names of TRUE negative signals, e.g. `["inactive_listing"]`; never sent to GHL), `notes` (evidence of UNKNOWN `rank_gap` / `competitor_gap`, e.g. "not in the top 60 on Maps for 'therapist austin'"; context for the rep, never sent to GHL), and empty `audit_summary` / `call_opener` for Claude to fill. `est_google_ads_spend` and `meta_ads_active` are left out (null, so part A writes nothing).

### 3.8 Claude step (nightly skill)

The skill tells the scheduled task to, for each candidate that is Hot, Warm, or Cold with an email:
1. Decide `franchise_or_chain` from the name, website and categories; if TRUE, drop with a reason (written to `dropped.jsonl`).
2. Write `audit_summary`: at most 3 short lines a rep can say out loud, facts only from `evidence`, "unknown" where data is missing.
3. Write `call_opener`: one sentence under 30 words naming the business and the primary signal; friendly, no hype.
4. Write only the two texts: each `qualified.jsonl` line is `{"bot_run_id", "audit_summary", "call_opener"}`.

`push` merges each line into its candidate (every other field comes from `candidates.jsonl`) and rejects a line that has any other key, a text that is empty or over its limit (summary over 3 lines or 400 characters; opener of 30 words or more), a `bot_run_id` already seen in the file (`duplicate line`), or a candidate that needs no text (`not eligible`).

### 3.9 Scheduling

One Claude Code scheduled task, daily at 02:00 local, prompt: run the `leadbot-nightly` skill for each configured city. First live push is never automatic: the task runs with `--dry-run` until the user switches the config flag `live = true` in `config/seeds.toml`.

## 4. Compliance

- DNC: the user registers for a National Do Not Call Registry Subscription Account Number at the FTC and downloads the area-code files for the target cities into `state/dnc/` (git-ignored). The three cities span about ten area codes (Austin 512, 737; Dallas 214, 469, 972, 945; Houston 713, 281, 832, 346); the first five are free, the rest are paid per area code, so the pilot may start with fewer. `collect` refuses to run for a city whose area codes have no file or when any loaded file is older than 31 days (the FTC scrub list must be no older than 31 days), drops listed numbers and numbers whose area code has no loaded file, and a live `push` loads the DNC files again, refuses them under the same 31-day age rule, and checks every lead against them. A `dnc_area_not_loaded` drop is not marked seen, so adding that area code's file brings the place back the next night. Not legal advice.
- No autodialing or SMS (unchanged from part A).
- Scraping Google Maps directly is against Google's terms of service; the SOP recommends providers. The user chose the open-source scraper with that risk; the bot keeps one worker and small nightly volumes.
- Only business contact data is stored.

## 5. Error handling

- Scraper exits non-zero, or every query returns 0 rows: stop the city, write `report.json` with `blocked_or_failed`, push nothing.
- Per-place enrichment errors become UNKNOWN; never crash the run.
- Push: part A's rules (an auth error stops the batch with exit 3; any other exception from `push_lead`, not only network errors, fails that lead as `failed` with `<ExceptionType>: <message>` and the batch continues). `push_report.json` is always written and seen marks are always applied; a failed lead is recorded in the seen store as `failed` (a retry record).
- `qualified.jsonl` is read line by line: a line that is not valid JSON or not a JSON object is recorded as `rejected` (`malformed line <n>`) and the other lines are still processed. The file is decoded per line (a UTF-8 BOM is accepted), so one bad byte rejects only that line. A malformed `candidates.jsonl` is a collect bug and still stops the push.
- Every artefact of a run stays in `state/runs/<run_id>/`; re-running `push` on the same file is safe (part A is idempotent).

## 6. Testing

- Unit tests (no network): seeds expansion, scraper row normalisation from the fixture, review-age parsing, each signal's TRUE/FALSE/UNKNOWN cases, scoring and tier boundaries (44/45, 69/70, cap), DNC lookup, filters and their order, qualified-line validation (tampered score rejected).
- Live dry run: Austin, `--dry-run`, user reads `qualified.jsonl` and `dropped.jsonl`.
- First live push: on the user's go-ahead, Austin only; every Hot lead checked by hand (SOP week 2).

## 7. Prerequisites from the user

1. FTC DNC registration and area-code files in `state/dnc/`.
2. Keep the Claude app open overnight on this PC (or accept next-launch runs).
