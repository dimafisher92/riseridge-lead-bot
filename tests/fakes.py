"""In-memory stand-in for GHLClient. Same method names and return shapes."""

import itertools


class FakeClient:
    def __init__(self, location_id="LOC1"):
        self.location_id = location_id
        self.fields, self.tags, self.pipelines = [], [], []
        self.contacts, self.opportunities = {}, {}
        self.calls = []
        self._ids = itertools.count(1)

    def _id(self, prefix):
        return f"{prefix}{next(self._ids)}"

    # setup
    def list_custom_fields(self):
        return list(self.fields)

    def create_custom_field(self, name, data_type):
        self.calls.append(("create_custom_field", name, data_type))
        f = {"id": self._id("f"), "name": name, "dataType": data_type, "picklistOptions": []}
        self.fields.append(f)
        return f

    def list_tags(self):
        return list(self.tags)

    def create_tag(self, name):
        self.calls.append(("create_tag", name))
        t = {"id": self._id("t"), "name": name}
        self.tags.append(t)
        return t

    def list_pipelines(self):
        return list(self.pipelines)

    def create_pipeline(self, name, stages):
        self.calls.append(("create_pipeline", name, tuple(stages)))
        p = {"id": self._id("p"), "name": name,
             "stages": [{"id": self._id("s"), "name": s} for s in stages]}
        self.pipelines.append(p)
        return p

    # contacts
    def find_duplicate(self, phone, email):
        for c in self.contacts.values():
            if (phone and c.get("phone") == phone) or (email and c.get("email") == email):
                return dict(c)
        return None

    def upsert_contact(self, body):
        self.calls.append(("upsert_contact", body))
        existing = self.find_duplicate(body.get("phone"), body.get("email"))
        if existing:
            c = self.contacts[existing["id"]]
            c.update({k: v for k, v in body.items() if k != "customFields"})
            c.setdefault("customFields", {}).update(
                {cf["id"]: cf["fieldValue"] for cf in body.get("customFields", [])})
            return dict(c), False
        cid = self._id("c")
        c = {k: v for k, v in body.items() if k != "customFields"}
        c.update(id=cid, tags=[],
                 customFields={cf["id"]: cf["fieldValue"] for cf in body.get("customFields", [])})
        self.contacts[cid] = c
        return dict(c), True

    def add_tags(self, contact_id, tags):
        self.calls.append(("add_tags", contact_id, tuple(tags)))
        c = self.contacts[contact_id]
        for t in tags:
            if t not in c["tags"]:
                c["tags"].append(t)
        return list(c["tags"])

    def get_contact(self, contact_id):
        c = self.contacts.get(contact_id)
        return dict(c) if c else None

    def delete_contact(self, contact_id):
        self.calls.append(("delete_contact", contact_id))
        self.contacts.pop(contact_id, None)

    # opportunities
    def search_opportunities(self, contact_id, *, pipeline_id=None, status=None):
        out = []
        for o in self.opportunities.values():
            if o["contactId"] != contact_id:
                continue
            if pipeline_id and o["pipelineId"] != pipeline_id:
                continue
            if status and status != "all" and o["status"] != status:
                continue
            out.append(dict(o))
        return out

    def create_opportunity(self, body):
        self.calls.append(("create_opportunity", body))
        oid = self._id("o")
        o = dict(body, id=oid)
        o.setdefault("lastStatusChangeAt", "2026-11-04T09:00:00.000Z")
        self.opportunities[oid] = o
        return dict(o)

    def get_opportunity(self, opportunity_id):
        o = self.opportunities.get(opportunity_id)
        return dict(o) if o else None

    def delete_opportunity(self, opportunity_id):
        self.calls.append(("delete_opportunity", opportunity_id))
        self.opportunities.pop(opportunity_id, None)
