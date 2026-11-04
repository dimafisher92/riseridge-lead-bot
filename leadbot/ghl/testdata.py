"""Twenty fictional leads for the live test (spec section 8).

Phones use +1 512-555-01NN, which is reserved for fiction; emails use example.com.
Order: 6 Hot (one per niche), 6 Warm, 4 Cold, 2 duplicates, 1 email-only Warm,
1 rejected. Seeds are pre-existing contacts the two duplicates collide with.
"""

from leadbot.ghl.mapping import SAMPLE_PAYLOAD

NICHES = ["Counselors / therapists", "Roofing", "HVAC", "Electricians",
          "Personal injury lawyers", "Med spas / dental"]

SEEDS = {
    "client": {"phone": "+15125550190", "name": "Test Seed Existing Client"},
    "lost": {"phone": "+15125550191", "name": "Test Seed Lost Deal"},
}

TIER_SCORE = {"Hot": 82, "Warm": 55, "Cold": 30}


def _lead(n, tier, niche, **changes):
    p = dict(SAMPLE_PAYLOAD)
    p.update(
        business_name=f"Test Lead {n:02d} {niche}",
        phone=f"+15125550{100 + n}",
        email=f"lead{n:02d}@example.com",
        website=f"https://lead{n:02d}.example.com",
        niche=niche,
        lead_tier=tier,
        lead_score=TIER_SCORE[tier],
        bot_run_id=f"test-2026-11-04-{n:02d}",
    )
    p.update(changes)
    return p


TEST_LEADS = (
    [_lead(n, "Hot", NICHES[n - 1]) for n in range(1, 7)]
    + [_lead(n, "Warm", NICHES[(n - 7) % 6]) for n in range(7, 13)]
    + [_lead(n, "Cold", NICHES[(n - 13) % 6]) for n in range(13, 17)]
    + [_lead(17, "Hot", "HVAC", phone=SEEDS["client"]["phone"]),
       _lead(18, "Warm", "Roofing", phone=SEEDS["lost"]["phone"]),
       _lead(19, "Warm", "Electricians", phone=None),
       _lead(20, "Hot", "HVAC", phone=None, email=None)]
)

EXPECTED_FIRST_RUN = {"created": 17, "duplicate": 2, "rejected": 1}
EXPECTED_REPEAT_RUN = {"existing": 13, "updated": 4, "duplicate": 2, "rejected": 1}
