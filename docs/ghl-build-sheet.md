# GHL build sheet (as built)

Sub-account: GSM Growth Agency (location `qxOMqnU9zjuxJ3xHHo90`), confirmed by the user on 2026-11-04 as the Lead Bot home.

## Custom fields (built 2026-11-04)

Folder "Lead Bot" (contact object) holds all 18 fields.

- 11 created by `scripts/setup_ghl.py` through the API, then moved into the folder in the UI: Lead Score, Target City, GBP URL, Review Count, Rating, Est. Google Ads Spend, Money Keyword, Current Rank, Audit Summary, Call Opener, Bot Run ID.
- 7 option fields created by hand in Settings → Custom Fields: Lead Tier, Niche, Primary Signal (Dropdown single), All Signals (Dropdown multiple), GBP Status, Call Outcome (Dropdown single), Meta Ads Active (Checkbox, one option "Yes").
- Type adjustments versus the plan: none. `setup_ghl.py --verify` reported 40 objects present, no drift.

UI notes for anyone repeating this: changing "Add to object" resets the folder and the option rows, so set the object first; the drawer opens slowly, so wait before clicking inside it.

## Client marker

No client tag exists in this sub-account. Decision (user, 2026-11-04): a contact is an existing client if it has any opportunity with status won (any pipeline) or any opportunity in the pipeline "02. GSM Appointment New Client". Configured as `[duplicate] client_pipelines` in `config/ghl_schema.toml`; `client_tags` stays empty. A live read-only `load_context` resolved the client pipeline.

## Reps by niche

Pending: the user maps reps to niches before the Route workflow is built.
