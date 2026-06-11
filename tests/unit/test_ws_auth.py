"""Unit tests for the WebSocket API-key resolution (subprotocol vs query)."""

from types import SimpleNamespace

from sentinex_api.routes.scans_ws import _API_KEY_SUBPROTOCOL, _resolve_api_key


def _ws(subprotocols):
    return SimpleNamespace(scope={"subprotocols": subprotocols})


def test_prefers_subprotocol_key():
    ws = _ws([_API_KEY_SUBPROTOCOL, "sx-secret-key"])
    key, negotiated = _resolve_api_key(ws, query_api_key="from-query")
    assert key == "sx-secret-key"
    assert negotiated == _API_KEY_SUBPROTOCOL


def test_falls_back_to_query_param():
    ws = _ws([])
    key, negotiated = _resolve_api_key(ws, query_api_key="from-query")
    assert key == "from-query"
    assert negotiated is None


def test_no_key_anywhere():
    ws = _ws([])
    key, negotiated = _resolve_api_key(ws, query_api_key=None)
    assert key is None
    assert negotiated is None


def test_ignores_unknown_subprotocol():
    ws = _ws(["some-other-proto", "value"])
    key, negotiated = _resolve_api_key(ws, query_api_key="q")
    assert key == "q"
    assert negotiated is None
