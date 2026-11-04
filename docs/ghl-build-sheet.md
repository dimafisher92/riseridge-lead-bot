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

## Workflows

The public API only lists workflows (`GET /workflows/`, checked 2026-11-04), so all are built in the UI. All stay in Draft until the workflow audit (plan Task 11 Step 1) is done with the user.

### [Lead Bot] Route (Draft, built 2026-11-04)

URL: `/v2/location/qxOMqnU9zjuxJ3xHHo90/automation/workflow/6e777db8-c587-46cb-8a31-8ec212a23182`

1. Trigger: Opportunity Created; filters: In pipeline is "Bot Leads – SEO", Status is Open.
2. Assign to user: Sergey Vitrenko ("only apply to unassigned contacts" off).
3. Add owner to opportunity: Sergey Vitrenko.
4. Internal Notification (in-app): title "New bot lead: {Lead Tier}", message pointing to the Lead Bot fields; redirect to Contact; to Assigned owners (contact owner).
5. Condition on Lead Tier: branch "Hot" (Lead Tier is Hot) / None (Warm; Cold leads never get an opportunity).
   - Hot: Add task "Call: {Primary Signal}", assigned to the contact's assigned user, due 1 day at 10:00 AM.
   - None: same task, due 2 days at 10:00 AM.

Differences from the spec: one rep, so no niche branches; the notification is in-app, not email; "Skip weekends" on the Hot task may not have saved (check in the test); task titles use the merge picker because typed `{{...}}` tags are dropped by the title box.

### [Lead Bot] Outcome (Draft, built 2026-11-04)

URL: `/v2/location/qxOMqnU9zjuxJ3xHHo90/automation/workflow/eb56f89a-8f0b-4e27-9a4a-cee6107a3d1c`

1. Trigger: Contact Changed, filter "Call Outcome has changed".
2. Find opportunity: most recently created, Pipeline is "Bot Leads – SEO". Not found → end. This is what limits the workflow to bot leads (only they have a deal in that pipeline); the trigger's tag filter only offers "tag added/removed", so a `bot-lead` filter was not possible there.
3. Condition on Call Outcome (Found branch):
   - No answer → Add task "Retry call: {Primary Signal}", contact's assigned user, due 2 days 10:00 AM.
   - Callback → Add task "Callback: {Primary Signal}", contact's assigned user, due 1 day 10:00 AM (rep moves the due time).
   - Interested → Update opportunity: pipeline Bot Leads – SEO, stage Audit Sent.
   - Not interested → Update opportunity: stage Nurture.
   - Closed → Update opportunity: stage Won, status won.
   - None → end.

Differences from the spec, to decide later: No answer does not yet count attempts (`bot-try-1..3`) or stop after 4 (the task text tells the rep to set Not interested after 4 tries); Not interested does not add `bot-nurture-90` (the Nurture stage is the hook for part C); part C placeholders (drip, audit email, onboarding) are not added as disabled steps.

### [Lead Bot] Lost status (Draft, built 2026-11-04)

URL: `/v2/location/qxOMqnU9zjuxJ3xHHo90/automation/workflow/0b55de62-4eac-4f0f-b8a0-ef76da6aa101`

Trigger: Pipeline stage changed, In pipeline "Bot Leads – SEO", Pipeline stage "Lost" → Update opportunity: status lost.

## Reps by niche

One rep covers every niche (user, 2026-11-04): Sergey Vitrenko. The Route workflow therefore uses a single Assign To User step instead of six niche branches; add branches when more reps join.
