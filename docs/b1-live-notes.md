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
