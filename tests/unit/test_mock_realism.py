"""Realism-hardening checks for the mock provider apps (Track B)."""
import time

from fastapi.testclient import TestClient

from sentinex_mocks.stripe import app as stripe_app
from sentinex_mocks.slack import app as slack_app
from sentinex_mocks.sendgrid import app as sendgrid_app
from sentinex_mocks.twilio import app as twilio_app


def test_stripe_ids_are_mixed_case():
    client = TestClient(stripe_app)
    resp = client.post("/v1/charges", json={"amount": 100})
    body = resp.json()
    assert body["id"].startswith("ch_")
    suffix = body["id"].split("_", 1)[1]
    assert any(c.isupper() for c in suffix) and any(c.islower() for c in suffix)


def test_stripe_headers_are_provider_plausible():
    client = TestClient(stripe_app)
    resp = client.post("/v1/charges", json={"amount": 100})
    assert resp.headers["server"] == "nginx"
    assert resp.headers["stripe-version"] == "2024-06-20"
    assert resp.headers["request-id"].startswith("req_")


def test_stripe_unmatched_route_uses_provider_error_shape():
    client = TestClient(stripe_app)
    resp = client.get("/v1/nonexistent-endpoint")
    assert resp.status_code == 404
    assert resp.json()["error"]["type"] == "invalid_request_error"


def test_stripe_known_404_unaffected_by_generic_handler():
    """get_customer's own JSONResponse 404 must still work unchanged."""
    client = TestClient(stripe_app)
    resp = client.get("/v1/customers/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["error"]["message"] == "No such customer"


def test_slack_unmatched_route_uses_flat_ok_false_shape():
    client = TestClient(slack_app)
    resp = client.get("/api/no.such.method")
    assert resp.status_code == 404
    assert resp.json() == {"ok": False, "error": "unknown_method"}


def test_sendgrid_unmatched_route_uses_errors_array_shape():
    client = TestClient(sendgrid_app)
    resp = client.get("/v3/no-such-endpoint")
    assert resp.status_code == 404
    assert "errors" in resp.json()


def test_twilio_unmatched_route_uses_code_message_shape():
    client = TestClient(twilio_app)
    resp = client.get("/no-such-endpoint")
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == 20404
    assert body["status"] == 404


def test_jitter_disabled_by_default():
    """MOCK_JITTER_MS is read once at app construction; the module-level
    ``stripe_app`` above was imported without it set, so requests should be
    fast — this is the CI/test default the middleware is designed for."""
    client = TestClient(stripe_app)
    start = time.monotonic()
    client.post("/v1/charges", json={"amount": 100})
    assert time.monotonic() - start < 0.05
