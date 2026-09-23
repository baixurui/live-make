"""At-least-once HTTP delivery; publishing owns all platform retry attempts."""
import json
from urllib.request import Request, build_opener, HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PublishingAdapter:
    def __init__(self, workflow, base_url, token, timeout=10):
        if not token or not base_url.startswith(("http://", "https://")):
            raise ValueError("publishing URL and service token required")
        self.workflow, self.base_url, self.token = workflow, base_url.rstrip("/"), token
        self.timeout = timeout
        self.opener = build_opener(NoRedirect())

    def _request(self, method, path, body=None):
        request = Request(self.base_url + path, method=method,
                          data=json.dumps(body).encode() if body is not None else None,
                          headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        with self.opener.open(request, timeout=self.timeout) as response:
            payload = response.read()
            return json.loads(payload) if payload else None

    def sync(self):
        # Pull first: a completed operation can be recovered even if its original
        # request acknowledgment was lost. State + inbox commit precedes remote ack.
        for event in self._request("GET", "/api/v1/internal/publishing/events"):
            self.workflow.receive(event)
            self._request("POST", f"/api/v1/internal/publishing/events/{event['event_id']}/ack")
        for event in self.workflow.pending():
            if event["event_type"] != "task.publish_requested.v1":
                continue
            receipt = self._request("POST", "/api/v1/internal/publishing/requests", event)
            if (receipt.get("receipt_id") != event["data"]["receipt_id"]
                    or receipt.get("idempotency_key") != event["idempotency_key"]
                    or receipt.get("status") not in {"PROCESSING", "SUCCEEDED", "FAILED", "ABORTED"}):
                raise ValueError("publishing returned an invalid acceptance receipt")
            self.workflow.acknowledge(event["event_id"])
