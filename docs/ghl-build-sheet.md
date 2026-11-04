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

The public API only lists workflows (`GET /workflows/`, checked 2026-11-04), so all are built in the UI. All three were published on 2026-11-04. The full audit of existing workflows (plan Task 11 Step 1) was skipped by the user's choice, because the sub-account has 165 published workflows; a one-lead live test replaced it (below).

### [Lead Bot] Route (published, built 2026-11-04)

URL: `/v2/location/qxOMqnU9zjuxJ3xHHo90/automation/workflow/6e777db8-c587-46cb-8a31-8ec212a23182`

1. Trigger: Opportunity Created; filters: In pipeline is "Bot Leads – SEO", Status is Open.
2. Assign to user: Sergey Vitrenko ("only apply to unassigned contacts" off).
3. Add owner to opportunity: Sergey Vitrenko.
4. Internal Notification (in-app): title "New bot lead: {Lead Tier}", message pointing to the Lead Bot fields; redirect to Contact; to Assigned owners (contact owner).
5. Condition on Lead Tier: branch "Hot" (Lead Tier is Hot) / None (Warm; Cold leads never get an opportunity).
   - Hot: Add task "Call: {Primary Signal}", assigned to the contact's assigned user, due 1 day at 10:00 AM.
   - None: same task, due 2 days at 10:00 AM.

Differences from the spec: one rep, so no niche branches; the notification is in-app, not email; "Skip weekends" on the Hot task may not have saved (check in the test); task titles use the merge picker because typed `{{...}}` tags are dropped by the title box.

### [Lead Bot] Outcome (published, built 2026-11-04)

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

### [Lead Bot] Lost status (published, built 2026-11-04)

URL: `/v2/location/qxOMqnU9zjuxJ3xHHo90/automation/workflow/0b55de62-4eac-4f0f-b8a0-ef76da6aa101`

Trigger: Pipeline stage changed, In pipeline "Bot Leads – SEO", Pipeline stage "Lost" → Update opportunity: status lost.

## Reps by niche

One rep covers every niche (user, 2026-11-04): Sergey Vitrenko. The Route workflow therefore uses a single Assign To User step instead of six niche branches; add branches when more reps join.

## One-lead live test (2026-11-04)

Pushed `TEST_LEADS[0]` (Hot, Counselors / therapists, tagged `bot-test`) with all three workflows published.

- Push: contact and opportunity created; 7 tags (`bot-lead`, `tier-hot`, `niche-counselors-therapists`, `sig-gbp`, `sig-ads`, `sig-site`, `bot-test`); 17 Lead Bot fields filled; Call Outcome not written.
- Route: contact owner and opportunity owner both Sergey Vitrenko; task "Call: GBP Suspended" assigned to him, due 2026-11-05 10:00 EST (next day; "Skip weekends" cannot be judged from a weekday push).
- Other workflows: the contact's Workflows panel listed no active enrolments, and its only conversation held two system activities ("Opportunity created", "Opportunity updated"): no SMS, email or call went out, and no foreign tag was added.
- Outcome: Call Outcome set to Interested through the API; the deal was in Audit Sent when first polled (under 15 s). Closed was deliberately not tested: a won deal triggers the live "SEO Calendar — Deal Won → Meta CAPI Purchase" workflow.
- Cleanup: contact and opportunity deleted. The first run reported a leftover opportunity because `/opportunities/search` still listed the deal right after the delete while `GET /opportunities/{id}` already returned 404; cleanup now confirms each search hit with a direct GET. The rerun reported CLEAN and removed the ledger.

Not yet exercised live: Warm branch, No answer / Callback / Not interested / Closed outcomes, Lost status, duplicates, Cold, and the 20-lead run.

## 20-lead live test (2026-11-04)

Client seed changed first (user decision): it carries the test-only tag `bot-test-client` instead of a won deal, and the push script adds that tag to the run's `client_tags`. Reason: the sub-account has about ten published won-deal workflows (for example "SEO Calendar — Deal Won → Meta CAPI Purchase", "Closed Deal (Won) -> Meta API", closed-deal Slack and bot notifications), so a test won deal could send a fake Purchase to Meta and a fake closed-deal alert to the team. The won-deal rule stays covered by unit tests; the live `status=all` search is still exercised by the lost seed.

- First run: MATCH, 17 created, 2 duplicate (lead 17 "existing client (tag bot-test-client)", lead 18 "deal lost on 2026-11-04"), 1 rejected (no phone or email).
- After the workflows ran (API check of every contact): all 6 Hot and 7 Warm (incl. the email-only lead) have Sergey as contact owner, one New/open deal owned by Sergey, and one call task: Hot due 2026-11-05 10:00 EST, Warm due 2026-11-06 10:00 EST. The 4 Cold have no deal and no task. Both seeds got `bot-dupe` and no `bot-lead`. No contact received any message other than GHL's opportunity activity entries.
- Repeat run: MATCH, 13 existing, 4 updated, 2 duplicate, 1 rejected; still one deal and one task per Hot/Warm contact.
- Cleanup: 19 contacts and 14 deals deleted, CLEAN, ledger removed.

Side effect: Sergey received 13 in-app "New bot lead" notifications during the run; the tasks were removed with the contacts.

Still not exercised live: No answer / Callback / Not interested / Closed outcomes, the Lost status workflow, and "Skip weekends" (a Friday push would show it).

## Outcome and Lost status live test (2026-11-04)

Pre-check: the three published stage-change workflows were opened in the builder. "LFF001. Stage Changed" is filtered to "Lead Form Pipeline", "VSL002. VSL - Stage Change" to "VSL Training", and "Pipeline Stage Changed to Pubbly" has 9 triggers, all on appointment bookings, "fb ad" tags or the "01. GSM Appointment" pipeline. None can fire on Bot Leads – SEO.

Four Hot test leads (01-04), one action each, checked 40 s later:

| Action | Result |
| --- | --- |
| Call Outcome = No answer | new task "Retry call: GBP Suspended", Sergey, due 2026-11-06 10:00 EST; deal stays New |
| Call Outcome = Callback | new task "Callback: GBP Suspended", Sergey, due 2026-11-05 10:00 EST; deal stays New |
| Call Outcome = Not interested | deal moved to Nurture, status open |
| Deal moved to stage Lost (API) | Lost status workflow set status lost |

No contact received any message other than GHL's opportunity activity entries. The original "Call:" task stays open after an outcome; the rep completes it. Cleanup: 4 contacts and 4 deals deleted, CLEAN.

Closed was not run live: it sets the deal to won, which would fire the account's won-deal workflows (Meta CAPI Purchase, closed-deal notifications). Its branch was checked in the builder only.
