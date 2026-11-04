"""Turns one bot payload (SOP Step 6 JSON) into GHL request bodies."""

from leadbot.ghl.schema import ALL_SIGNALS_FIELD, PRIMARY_FIELD, niche_tag, tier_tag

SAMPLE_PAYLOAD = {
    "business_name": "Lone Star Cooling",
    "phone": "+15125550142",
    "email": "info@lonestarcooling.com",
    "website": "https://lonestarcooling.com",
    "niche": "HVAC",
    "city": "Austin, TX",
    "gbp_url": "https://maps.google.com/?cid=1",
    "gbp_status": "Unverified",
    "review_count": 14,
    "rating": 4.2,
    "est_google_ads_spend": 4500,
    "meta_ads_active": True,
    "money_keyword": "ac repair austin",
    "current_rank": 23,
    "signals": ["gbp_unverified_or_suspended", "google_ads_spend", "broken_website"],
    "primary_signal": "GBP Suspended",
    "lead_score": 82,
    "lead_tier": "Hot",
    "audit_summary": "Listing shows Claim this business. Ranks #23 for ac repair austin while bidding on it. No conversion tag on site.",
    "call_opener": "Hi, I noticed Lone Star Cooling's Google listing isn't verified, so it's hiding you from calls you're already paying ads for.",
    "bot_run_id": "2026-11-03-hvac-austin-0142",
}


class PayloadError(ValueError):
    pass


def _empty(value):
    return value is None or value == "" or value == []


def signal_categories(payload, schema):
    out = []
    for flag in payload.get("signals") or []:
        if flag not in schema.signals:
            raise PayloadError(f"unknown signal {flag!r}")
        category = schema.signals[flag]
        if category not in out:
            out.append(category)
    return out


def primary_category(payload, schema):
    value = payload.get("primary_signal")
    if _empty(value):
        return None
    if value in schema.signal_tags:
        return value
    if value in schema.signals:
        return schema.signals[value]
    raise PayloadError(f"primary_signal {value!r} is neither a category nor a signal flag")


def validate_payload(payload, schema):
    if _empty(payload.get("business_name")):
        raise PayloadError("business_name is required")
    if _empty(payload.get("phone")) and _empty(payload.get("email")):
        raise PayloadError("phone or email is required")
    if payload.get("lead_tier") not in schema.tiers:
        raise PayloadError(f"lead_tier {payload.get('lead_tier')!r} is not one of {schema.tiers}")
    if payload.get("niche") not in schema.niches:
        raise PayloadError(f"niche {payload.get('niche')!r} is not one of {schema.niches}")
    if _empty(payload.get("bot_run_id")):
        raise PayloadError("bot_run_id is required")
    signal_categories(payload, schema)
    primary_category(payload, schema)


def field_values(payload, schema):
    values = {}
    for spec in schema.fields:
        if not spec.source:
            continue
        if spec.name == ALL_SIGNALS_FIELD:
            value = signal_categories(payload, schema)
        elif spec.name == PRIMARY_FIELD:
            value = primary_category(payload, schema)
        elif spec.data_type == "CHECKBOX":
            if spec.source not in payload:
                continue
            value = [spec.options[0]] if payload[spec.source] else []
            values[spec.name] = value
            continue
        else:
            value = payload.get(spec.source)
        if _empty(value):
            continue
        values[spec.name] = value
    return values


def _split_city(city):
    if not city:
        return None, None
    if "," in city:
        name, state = city.split(",", 1)
        return name.strip(), state.strip()
    return city.strip(), None


def contact_body(payload, schema, field_ids, location_id):
    city, state = _split_city(payload.get("city"))
    body = {
        "locationId": location_id,
        "name": payload["business_name"],
        "companyName": payload["business_name"],
        "phone": payload.get("phone"),
        "email": payload.get("email"),
        "website": payload.get("website"),
        "city": city,
        "state": state,
        "country": "US",
        "source": "Lead Bot",
        "customFields": [
            {"id": field_ids[name], "fieldValue": value}
            for name, value in field_values(payload, schema).items()
        ],
    }
    return {k: v for k, v in body.items() if not _empty(v) or k == "customFields"}


def lead_tags(payload, schema):
    tags = ["bot-lead", tier_tag(payload["lead_tier"]), niche_tag(payload["niche"])]
    tags += [schema.signal_tags[c] for c in signal_categories(payload, schema)]
    return tags
