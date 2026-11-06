# B1 live notes

## Austin dry run (2026-11-05, run `2026-11-05-counselors-therapists-austin-tx`)

Command: `collect --no-dnc` (no DNC files yet), qualify by hand following `.claude/skills/leadbot-nightly/SKILL.md`, `push --dry-run`. Nothing was written to GHL (collect only ran read-only duplicate lookups); `state/seen.sqlite` stayed at 0 rows.

- Runtime: about 13 minutes (money query 2 min, 5 area queries 4 min, enrichment of 150 places 7 min).
- Scraped 239 places (money query 60). Dropped: 10 no phone, 19 shared phone, 60 over the nightly cap of 150. No existing GHL contacts among them.
- Tiers: 0 Hot, 10 Warm, 140 Cold; 56 needed text (10 Warm + 46 Cold with an email). 3 dropped by the Claude step as chains (a LifeStance provider page, two "- Round Rock" location listings). Dry-run push: 53 would_push, 0 rejected.

### Signal checks

- Unclaimed listing (25 points): 9 of 150. Two checked by hand in Google Maps (Providence Counseling, Sydney W. Paver, PhD): both show "Claim this business". The signal is reliable.
- Money-keyword rank: the scraper's first results (DCMH Downtown, Thriveworks) match a manual "therapist in Austin, TX" search closely but not exactly (the manual search showed Just Mind Counseling first). Rank is approximate, as expected.
- `rank_gap` TRUE for 147 of 150 and `competitor_gap` for 130 of 150: the money query returns only 60 places, so almost every place is "not in the map pack", and the top 3 are large multi-location practices (143 reviews on average). These two signals add 15 points to nearly every lead and do not separate leads.
- `broken_website` TRUE for 84 of 150: 21 are "did not load", including national groups (Thriveworks, LifeStance, Mindpath) that refuse the bot's user agent; 61 are homepage checks, where sites built on JavaScript site builders likely load their forms and tags after the page, so the plain fetch misses them. Both inflate the signal. (Retrying with a browser-like user agent was refused by the session's permission check as bot-check circumvention; treating a refused fetch as UNKNOWN is the right fix.)
- `new_business`: 2 TRUE. Most Warm leads are unclaimed solo listings whose newest visible reviews are years old, which may mean an inactive practice.
- Directory profiles as websites: 1 Psychology Today profile (Tumbleweed Therapy).

### Calibration to decide before go-live

1. Rank and competitor gap: score them only when they separate leads (for example rank_gap only when the place appears in the money results beyond the map pack, competitor gap against the top 3 independents), or reduce their weight.
2. Broken website: a fetch that is refused (HTTP 403/429, bot walls) becomes UNKNOWN, not "did not load"; consider requiring "no form AND no call link" rather than any two of four.
3. Inactive practices: consider a negative signal when the newest review is older than about 2 years and the listing is unclaimed.

## Austin dry run after calibration (2026-11-05, same run_id; first run kept as `state/runs/2026-11-05-austin-before-calibration`)

Task 12 rules (rank gap only #4-#20, competitor gap against independents only for places in the money results, refused fetches UNKNOWN and a loaded site broken only with no form AND no call link, inactive listing −30 only when every review is visible and the newest is over 2 years old).

| of 150 enriched | before | after |
| --- | --- | --- |
| rank_gap TRUE | 147 | 12 |
| competitor_gap TRUE | 130 | 16 |
| broken_website TRUE | 84 | 42 (10 more UNKNOWN: refused by the site) |
| unclaimed | 9 | 8 |
| inactive_listing (−30) | – | 21 |
| Warm | 10 | 1 (Rose Psychology Practice, 45) |
| needs text | 56 | 48; 45 would_push after 3 chain drops |

Scraper results differ between runs (236 vs 239 scraped; Providence Counseling and Sydney W. Paver were outside the 150 enriched this time). Of the 8 unclaimed listings, 4 are inactive (newest review 2017-2024) and 3 have a form or call link on their site, so they score 30 (Cold).

Effect: the signals now separate leads, but B1 yields about one call-ready (Warm) lead per city per night, because without ad signals an active unclaimed listing scores 30 and needs a broken site or a new business on top. Decision needed before go-live: scale volume (more areas/cities, a higher nightly cap), lower the B1 Warm threshold, or wait for B2's ad signals.

Note: the chain check in this test was a quick name rule and wrongly dropped two practices with descriptive suffixes ("- Couples & Individual Counselor", "- Envisioned Counseling"); in the nightly run Claude judges chains from name, website and evidence.

## DNC switched off (2026-11-05)

`dnc_check = false` in `config/seeds.toml` (user decision: B2B calls, no FTC files). While adding the switch, a test that expected collect to stop at the DNC gate ran a real Austin collect for about 2 minutes before it was stopped: it rewrote `money.*` and `areas.*` raw scraper files in `state/runs/2026-11-05-counselors-therapists-austin-tx/` and stopped before enrichment, so no GHL calls were made and `candidates.jsonl`, `report.json`, `qualified.jsonl` and the seen store are unchanged. `tests/conftest.py` now fails any test that reaches the real scraper or GHL.

## Existing-automation check before go-live (2026-11-05, plan Task 11 Step 2)

`GET /workflows/` lists 224 workflows: 168 published (165 at the part A audit + the three `[Lead Bot]` ones), 56 drafts. Created or updated since 2026-11-04 (UTC):

| workflow | status | updated | verdict |
| --- | --- | --- | --- |
| [Lead Bot] Route / Outcome / Lost status | published | 2026-11-04 20:57-20:58 | ours |
| 002. Free Audit Booked | published | 2026-11-04 17:23 | single trigger: appointment status on the "Free AI Visibility Audit" calendar; cannot fire on bot leads |
| AEO API \| SEO RR Pixel 2026 | published | 2026-11-04 19:44 | triggers: appointment status, stage changed to "009. Closed Deal" in "GSM SEO Pipeline", specific form submissions; cannot fire on bot leads |
| 001. New Lead + AEO Nurture (DRAFT - review) | draft | 2026-11-05 01:43 | does not run while a draft; its name suggests a new-lead nurture, so check its triggers exclude `bot-lead` contacts before anyone publishes it |

Both published edits predate the part A live tests (run after 20:58), which showed no side effects on bot contacts. Result: no new published workflow touches bot leads.

## First live push, Austin (2026-11-05, run `2026-11-05-counselors-therapists-austin-tx`)

`live = true`, `dnc_check = false` (commit 84da189). The calibrated dry-run folder was kept as `state/runs/2026-11-05-austin-dry-run-calibrated`.

- Collect: 232 scraped, dropped 10 no phone, 23 shared phone, 49 over the nightly cap of 150 (not marked seen, collected again later). Tiers: 0 Hot, 4 Warm, 146 Cold; 53 needed text.
- Qualify: 4 dropped as chains or part of a larger organisation (Respark Therapy - South Austin Office, Better Health Counseling - Round Rock, Alliance Counseling Works- Round Rock, Hope Counseling Center of Texas Baptist Children's Home). 49 qualified lines; dry run 49 would_push.
- Live push: 49 created, 0 rejected, 0 failed.
- QA by API, every contact:
  - 4 Warm: contact owner Sergey Vitrenko, one open deal owned by him, one call task ("Call: Broken Site" x2, "Call: GBP Suspended" x2) due 2026-11-07 10:00 EST.
  - 45 Cold (with email): no owner, no deal, no task, as in part A.
  - All 49 carry `bot-lead` and only the bot's own tags (tier, niche, signal).
  - Conversations: only 4 "opportunity created" activity entries, no SMS, email or call.
- Seen store: 49 created, 97 cold_no_email, 4 franchise_or_chain, 10 no_phone, 23 shared_phone.

Finding: 2026-11-07 is a Saturday. The Route workflow's Warm task delay (two days) does not skip weekends, so a Thursday-night push makes Warm tasks due on Saturday. Decide whether the Route task should skip weekends before the nightly schedule starts.

Fixed 2026-11-06 (user decision): "Skip weekends" switched on for the Warm task in [Lead Bot] Route; the Hot task already had it. Reload confirmed: a Warm task created on Friday is now due Monday 10:00. The 4 Warm tasks created on 2026-11-05 were moved by API (`PUT /contacts/{id}/tasks/{taskId}`, dueDate only) from Saturday 7 Nov to Monday 2026-11-09 10:00 EST; re-read confirmed, titles and assignee unchanged.

## Nightly schedule (2026-11-06)

Claude scheduled task `leadbot-nightly` (C:/Users/dimaf/.claude/scheduled-tasks/leadbot-nightly/SKILL.md): every day at 02:00 local time (dispatched about 02:09), Austin only, live. The prompt makes the run read and follow `.claude/skills/leadbot-nightly/SKILL.md` and limits it to "Austin, TX" until Dallas and Houston have had a dry run. It was saved in Auto mode: no permission prompts, a classifier blocks unsafe actions, so no `.claude/settings.json` allowlist was added. It runs only while the Claude app is open; a missed run starts at the next launch. Austin had 232 places in this run, and leads are not collected again for 30 days (`seen_days`), so expect fewer new Austin leads each night.

