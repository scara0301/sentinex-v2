"""
Tests for the recording proxy's request/response classifier.

Two behaviors here are load-bearing for the whole product:

1. Requests to hosts we do not recognize must still be recorded. The built-in
   scenarios detect exfiltration by matching the host an agent contacted
   (``host_contains: [evil-archive]``), so dropping unknown hosts would make
   a fully-compliant agent look clean.
2. JSON bodies that are not objects (arrays, strings, numbers) must not raise.
   An exception here escapes the mitmproxy hook and the event is lost.
"""

import pytest

pytest.importorskip("mitmproxy")

from sentinex_proxy.interceptors.http import (  # noqa: E402
    classify_request,
    classify_response,
)


class _Request:
    def __init__(self, host, path="/", method="GET", content=b"", query=None):
        self.host = host
        self.pretty_host = host
        self.path = path
        self.method = method
        self.content = content
        self.query = query or {}


class _Response:
    def __init__(self, content=b"", status_code=200):
        self.content = content
        self.status_code = status_code


class _Flow:
    def __init__(self, request, response=None):
        self.request = request
        self.response = response


# ---------------------------------------------------------------------------
# Unknown hosts are recorded (the exfiltration-detection requirement)
# ---------------------------------------------------------------------------

def test_records_unknown_host():
    flow = _Flow(_Request("files.evil-archive.sentinex.test", "/upload", "POST"))
    result = classify_request(flow)
    assert result is not None
    assert result["host"] == "files.evil-archive.sentinex.test"
    assert result["provider"] == "http"


def test_unknown_host_tool_name_carries_the_host():
    flow = _Flow(_Request("attacker.example", "/x", "POST"))
    assert classify_request(flow)["tool"] == "http.post_attacker.example"


def test_known_provider_still_detected():
    flow = _Flow(_Request("api.stripe.com", "/v1/refunds", "POST"))
    result = classify_request(flow)
    assert result["provider"] == "stripe"
    assert result["tool"] == "stripe.post_refunds"


def test_tool_name_separates_method_and_operation():
    """Regression: names used to concatenate into 'stripe.postrefunds'."""
    flow = _Flow(_Request("api.stripe.com", "/v1/refunds", "POST"))
    assert "post_refunds" in classify_request(flow)["tool"]


def test_object_id_is_skipped_in_tool_name():
    flow = _Flow(_Request("api.stripe.com", "/v1/customers/cus_NffrFeUfNV2Hib"))
    assert classify_request(flow)["tool"] == "stripe.get_customers"


def test_numeric_id_is_skipped_in_tool_name():
    flow = _Flow(_Request("api.stripe.com", "/v1/customers/12345"))
    assert classify_request(flow)["tool"] == "stripe.get_customers"


def test_scenario_glob_still_matches_generated_names():
    """Built-in scenarios inject on 'stripe.*'; naming must stay compatible."""
    from fnmatch import fnmatch

    flow = _Flow(_Request("api.stripe.com", "/v1/charges", "POST"))
    assert fnmatch(classify_request(flow)["tool"], "stripe.*")


# ---------------------------------------------------------------------------
# Non-object JSON bodies must not raise
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "body",
    [b'[1, 2, 3]', b'"a string"', b"42", b"true", b"null"],
)
def test_non_object_request_body_is_wrapped(body):
    flow = _Flow(_Request("api.stripe.com", "/v1/charges", "POST", content=body))
    args = classify_request(flow)["args"]
    assert isinstance(args, dict)


def test_array_body_with_query_params_does_not_raise():
    """Regression: args.update() raised AttributeError on a list body."""
    flow = _Flow(
        _Request(
            "api.stripe.com",
            "/v1/charges",
            "POST",
            content=b"[1, 2]",
            query={"limit": "10"},
        )
    )
    args = classify_request(flow)["args"]
    assert isinstance(args, dict)
    assert args["query"] == {"limit": "10"}


def test_query_params_do_not_clobber_body_fields():
    flow = _Flow(
        _Request(
            "api.stripe.com",
            "/v1/charges",
            "POST",
            content=b'{"amount": 100}',
            query={"amount": "999"},
        )
    )
    args = classify_request(flow)["args"]
    assert args["amount"] == 100
    assert args["query"]["amount"] == "999"


def test_non_json_body_is_captured_as_text():
    flow = _Flow(
        _Request("api.stripe.com", "/v1/charges", "POST", content=b"amount=100&x=1")
    )
    assert "amount=100" in classify_request(flow)["args"]["body"]


def test_array_response_body_is_wrapped():
    flow = _Flow(
        _Request("api.slack.com", "/api/x"),
        _Response(content=b'[{"id": 1}]'),
    )
    result = classify_response(flow)
    assert isinstance(result["response"], dict)
    assert result["status_code"] == 200


def test_object_response_body_passes_through():
    flow = _Flow(_Request("api.slack.com", "/api/x"), _Response(content=b'{"ok": true}'))
    assert classify_response(flow)["response"] == {"ok": True}


def test_missing_response_returns_empty():
    assert classify_response(_Flow(_Request("api.slack.com", "/api/x"))) == {}


# ---------------------------------------------------------------------------
# Payloads produced here must satisfy the event schema
# ---------------------------------------------------------------------------

def test_classified_request_validates_against_event_schema():
    from sentinex_core.events.schema import ToolCallPayload

    flow = _Flow(_Request("evil.example", "/steal", "POST", content=b"[1,2]"))
    c = classify_request(flow)
    payload = ToolCallPayload(
        agent_id="unknown", tool=c["tool"], args=c["args"], host=c["host"]
    )
    assert payload.host == "evil.example"


def test_classified_response_validates_against_event_schema():
    from sentinex_core.events.schema import ToolResultPayload

    flow = _Flow(_Request("evil.example", "/steal"), _Response(content=b'["a"]'))
    c = classify_response(flow)
    payload = ToolResultPayload(chain_id="c", ok=True, response=c["response"])
    assert isinstance(payload.response, dict)
