"""Minimal GoHighLevel API v2 client. Stdlib only; the token never leaves the headers."""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://services.leadconnectorhq.com"
API_VERSION = "2021-07-28"
SCOPE_HINT = ("Check the Private Integration token scopes: contacts, opportunities, "
              "custom fields and tags (read and write), and pipelines.")


class GHLError(Exception):
    def __init__(self, status, message):
        super().__init__(f"GHL {status}: {message}")
        self.status = status
        self.message = message


class GHLAuthError(GHLError):
    pass


def urllib_transport(method, url, headers, body):
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()


def _decode(raw):
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        return {"message": raw.decode("utf-8", "replace")[:200]}


def _message(payload):
    msg = payload.get("message") if isinstance(payload, dict) else None
    if isinstance(msg, list):
        return "; ".join(str(m) for m in msg)
    if msg:
        return str(msg)
    return str(payload)[:200]


def _retry_delay(headers, attempt):
    for key, value in headers.items():
        if key.lower() == "retry-after":
            try:
                return float(value)
            except ValueError:
                break
    return 2 ** attempt


class GHLClient:
    def __init__(self, token, location_id, *, base_url=BASE_URL,
                 transport=urllib_transport, sleep=time.sleep, max_retries=5):
        self._token = token
        self.location_id = location_id
        self.base_url = base_url.rstrip("/")
        self._transport = transport
        self._sleep = sleep
        self.max_retries = max_retries

    def request(self, method, path, *, query=None, body=None):
        url = self.base_url + path
        if query:
            clean = {k: v for k, v in query.items() if v is not None}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Version": API_VERSION,
            "Accept": "application/json",
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        for attempt in range(self.max_retries + 1):
            status, resp_headers, raw = self._transport(method, url, headers, data)
            if status == 429 or status >= 500:
                if attempt == self.max_retries:
                    raise GHLError(status, f"gave up after {self.max_retries} retries")
                self._sleep(_retry_delay(resp_headers, attempt))
                continue
            payload = _decode(raw)
            if status in (401, 403):
                raise GHLAuthError(status, f"{_message(payload)}. {SCOPE_HINT}")
            if status >= 400:
                raise GHLError(status, _message(payload))
            return payload
        raise GHLError(0, "unreachable")

    # Custom fields, tags, pipelines -----------------------------------------
    def list_custom_fields(self):
        data = self.request("GET", f"/locations/{self.location_id}/customFields",
                            query={"model": "contact"})
        return data.get("customFields", [])

    def create_custom_field(self, name, data_type):
        data = self.request("POST", f"/locations/{self.location_id}/customFields",
                            body={"name": name, "dataType": data_type, "model": "contact"})
        return data.get("customField", data)

    def list_tags(self):
        return self.request("GET", f"/locations/{self.location_id}/tags").get("tags", [])

    def create_tag(self, name):
        data = self.request("POST", f"/locations/{self.location_id}/tags", body={"name": name})
        return data.get("tag", data)

    def list_pipelines(self):
        data = self.request("GET", "/opportunities/pipelines",
                            query={"locationId": self.location_id})
        return data.get("pipelines", [])

    def create_pipeline(self, name, stages):
        body = {
            "name": name,
            "locationId": self.location_id,
            "stages": [{"name": s, "position": i} for i, s in enumerate(stages)],
        }
        return self.request("POST", "/opportunities/pipelines", body=body)

    # Contacts ----------------------------------------------------------------
    def find_duplicate(self, phone, email):
        data = self.request("GET", "/contacts/search/duplicate", query={
            "locationId": self.location_id, "number": phone, "email": email})
        return data.get("contact") or None

    def upsert_contact(self, body):
        data = self.request("POST", "/contacts/upsert", body=body)
        return data["contact"], bool(data.get("new"))

    def add_tags(self, contact_id, tags):
        data = self.request("POST", f"/contacts/{contact_id}/tags", body={"tags": list(tags)})
        return data.get("tags", [])

    def get_contact(self, contact_id):
        try:
            return self.request("GET", f"/contacts/{contact_id}").get("contact")
        except GHLError as exc:
            if exc.status in (400, 404):
                return None
            raise

    def delete_contact(self, contact_id):
        self.request("DELETE", f"/contacts/{contact_id}")

    # Opportunities -----------------------------------------------------------
    def search_opportunities(self, contact_id, *, pipeline_id=None, status=None):
        data = self.request("GET", "/opportunities/search", query={
            "location_id": self.location_id, "contact_id": contact_id,
            "pipeline_id": pipeline_id, "status": status, "limit": 100})
        return data.get("opportunities", [])

    def create_opportunity(self, body):
        data = self.request("POST", "/opportunities/", body=body)
        return data.get("opportunity", data)

    def delete_opportunity(self, opportunity_id):
        self.request("DELETE", f"/opportunities/{opportunity_id}")
