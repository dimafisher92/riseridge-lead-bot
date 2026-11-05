---
name: leadbot-nightly
description: Nightly RiseRidge lead bot run (part B1). Collects counselor/therapist leads per city, writes the audit summary and call opener for each lead that needs one, then pushes to GoHighLevel or dry-runs. Use when the scheduled task "leadbot-nightly" fires or the user asks to run the lead bot.
---

# Lead bot nightly run

Repository: `D:/Claude Code/riseridge-leadbot`. Run every command from there with `python`. Never print or open `.env`.

## 1. Collect, once per city

Cities: every `[cities."..."]` table in `config/seeds.toml`, in file order. Niche: `Counselors / therapists`.

```
python -m leadbot.bot collect --niche "Counselors / therapists" --city "<city>"
```

- Exit 0: continue with that city.
- Exit 2 (`blocked_or_failed`): note it for the report and skip the city. Do not retry in the same run.
- Stops with "No DNC file for area codes": if `config/seeds.toml` has `live = false`, rerun the same command with `--no-dnc`. If `live = true`, skip the city and report the missing area codes.

The printed report names the `run_id`; the run folder is `state/runs/<run_id>/`.

## 2. Qualify

Read `state/runs/<run_id>/candidates.jsonl`. Work only on records whose `lead_tier` is Hot or Warm, or Cold with a non-empty `email`.

For each such record:
1. **Chain or franchise?** Decide from `business_name`, `website` and `evidence`: national or regional brands, names with a location suffix ("- Austin"), practices that list many locations. If yes, append one line to `dropped.jsonl` in the same folder: `{"place_id": ..., "business_name": ..., "reason": "franchise_or_chain", "note": "<why>"}` and do not qualify it.
2. Otherwise copy the record exactly and fill only:
   - `audit_summary`: at most 3 short lines separated by newlines, 400 characters in total, facts a rep can say out loud. Use only what `evidence` states. Never invent numbers. If a needed fact is missing, say "unknown".
   - `call_opener`: one sentence under 30 words that names the business and its primary signal. Friendly, no hype.
3. Never change any other field. `push` rejects a record whose signals, score, tier, phone or other fields differ from the candidate.

Write all qualified records, one JSON object per line, UTF-8, to `state/runs/<run_id>/qualified.jsonl` in one write.

## 3. Push

- `live = false` in `config/seeds.toml`: `python -m leadbot.bot push state/runs/<run_id>/qualified.jsonl --dry-run`
- `live = true`: `python -m leadbot.bot push state/runs/<run_id>/qualified.jsonl`

A non-zero exit means some lines were rejected or failed. Read `dry_run_report.json` or `push_report.json` and include each reason in the report. Exit 3 means GoHighLevel rejected the token: stop the whole run (do not collect or push any further city) and say in the report that the GHL token needs attention.

## 4. Report

One short message: whether the run was live; per city the scraped count, drops by reason, tiers, qualified, pushed (or would_push), rejected and failed. Name only Hot leads, by business name. No phone numbers or emails.
