"""Verified, tag-guarded removal of live test data.

Candidates come from the ledger AND from a duplicate lookup on every test phone
and email, so a test contact that never reached the ledger is still found.
A contact without the bot-test tag is never deleted, and neither are its
opportunities. Every deletion is followed by a read-back check.
"""

from dataclasses import dataclass, field

from leadbot.ghl import testdata
from leadbot.ghl.client import GHLError

TEST_TAG = "bot-test"


@dataclass
class CleanupResult:
    contacts_deleted: list = field(default_factory=list)
    opportunities_deleted: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    leftovers: list = field(default_factory=list)

    @property
    def clean(self):
        return not self.leftovers


def _unique(items):
    seen, out = set(), []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _test_identifiers():
    phones = [lead.get("phone") for lead in testdata.TEST_LEADS]
    phones += [seed["phone"] for seed in testdata.SEEDS.values()]
    emails = [lead.get("email") for lead in testdata.TEST_LEADS]
    return _unique(phones), _unique(emails)


def _discover(client, ledger):
    found = list(ledger.get("contacts", []))
    phones, emails = _test_identifiers()
    for phone in phones:
        dup = client.find_duplicate(phone, None)
        if dup:
            found.append(dup["id"])
    for email in emails:
        dup = client.find_duplicate(None, email)
        if dup:
            found.append(dup["id"])
    return _unique(found)


def _opp_ids(client, contact_id):
    return [o["id"] for o in client.search_opportunities(contact_id, status="all")]


def cleanup_test_data(client, ledger):
    result = CleanupResult()

    deletable, protected_contacts = [], []
    for cid in _discover(client, ledger):
        contact = client.get_contact(cid)
        if contact is None:
            continue
        if TEST_TAG in (contact.get("tags") or []):
            deletable.append(cid)
        else:
            result.skipped.append(cid)
            protected_contacts.append(cid)

    protected_opps = set()
    for cid in protected_contacts:
        protected_opps.update(_opp_ids(client, cid))

    opp_ids = list(ledger.get("opportunities", []))
    for cid in deletable:
        opp_ids += _opp_ids(client, cid)
    for oid in _unique(opp_ids):
        if oid in protected_opps:
            continue
        try:
            client.delete_opportunity(oid)
        except GHLError as exc:
            if exc.status != 404:
                raise
        result.opportunities_deleted.append(oid)

    for cid in deletable:
        for oid in _opp_ids(client, cid):
            result.leftovers.append(f"opportunity {oid} still on contact {cid}")

    for cid in deletable:
        client.delete_contact(cid)
        result.contacts_deleted.append(cid)
    for cid in deletable:
        if client.get_contact(cid) is not None:
            result.leftovers.append(f"contact {cid} still exists")

    return result
