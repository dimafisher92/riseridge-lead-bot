"""push_lead(): the one entry point the bot (part B) calls."""

import datetime as dt
from dataclasses import dataclass

from leadbot.ghl.client import GHLAuthError, GHLError
from leadbot.ghl.mapping import PayloadError, contact_body, lead_tags, validate_payload


class SetupIncomplete(RuntimeError):
    pass


@dataclass
class Context:
    field_ids: dict
    pipeline_id: str
    stage_ids: dict


@dataclass
class PushResult:
    outcome: str
    contact_id: str = None
    opportunity_id: str = None
    message: str = ""


def load_context(client, schema):
    by_name = {f["name"]: f["id"] for f in client.list_custom_fields()}
    missing = [f.name for f in schema.fields if f.name not in by_name]
    pipeline = next((p for p in client.list_pipelines()
                     if p["name"].lower() == schema.pipeline_name.lower()), None)
    if pipeline is None:
        missing.append(f"pipeline {schema.pipeline_name}")
    elif schema.entry_stage not in {s["name"] for s in pipeline.get("stages", [])}:
        missing.append(f"stage {schema.entry_stage} in pipeline {schema.pipeline_name}")
    if missing:
        raise SetupIncomplete("run scripts/setup_ghl.py; missing: " + ", ".join(missing))
    stage_ids = {s["name"]: s["id"] for s in pipeline.get("stages", [])}
    return Context({f.name: by_name[f.name] for f in schema.fields}, pipeline["id"], stage_ids)


def _status_date(opportunity):
    raw = opportunity.get("lastStatusChangeAt") or opportunity.get("updatedAt")
    if not raw:
        return None
    return dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).date()


def _duplicate_reason(client, schema, existing, today):
    tags = {t.lower() for t in existing.get("tags") or []}
    for marker in schema.client_tags:
        if marker.lower() in tags:
            return f"existing client (tag {marker})"
    cutoff = today - dt.timedelta(days=schema.lost_window_days)
    for opp in client.search_opportunities(existing["id"], status="lost"):
        changed = _status_date(opp)
        if changed and changed >= cutoff:
            return f"deal lost on {changed.isoformat()}"
    return None


def push_lead(client, schema, ctx, payload, *, extra_tags=(), today=None):
    today = today or dt.datetime.now(dt.timezone.utc).date()
    try:
        validate_payload(payload, schema)
    except PayloadError as exc:
        return PushResult("rejected", message=str(exc))

    contact_id = None
    try:
        existing = client.find_duplicate(payload.get("phone"), payload.get("email"))
        if existing:
            reason = _duplicate_reason(client, schema, existing, today)
            if reason:
                client.add_tags(existing["id"], ["bot-dupe"])
                return PushResult("duplicate", existing["id"], message=reason)

        contact, is_new = client.upsert_contact(
            contact_body(payload, schema, ctx.field_ids, client.location_id))
        contact_id = contact["id"]
        client.add_tags(contact_id, lead_tags(payload, schema) + list(extra_tags))
        outcome = "created" if is_new else "updated"

        if payload["lead_tier"] == "Cold":
            return PushResult(outcome, contact_id)

        open_opps = client.search_opportunities(contact_id, pipeline_id=ctx.pipeline_id,
                                                status="open")
        if open_opps:
            return PushResult("existing", contact_id, open_opps[0]["id"])

        opp = client.create_opportunity({
            "pipelineId": ctx.pipeline_id,
            "pipelineStageId": ctx.stage_ids[schema.entry_stage],
            "locationId": client.location_id,
            "name": f"{payload['business_name']} – {payload['niche']}",
            "status": "open",
            "contactId": contact_id,
        })
        return PushResult(outcome, contact_id, opp["id"])
    except GHLAuthError:
        raise  # a scope problem fails every lead: stop the batch (spec section 7)
    except GHLError as exc:
        return PushResult("failed", contact_id, message=str(exc))
