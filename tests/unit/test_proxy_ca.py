import os

from cryptography import x509
from cryptography.x509.oid import NameOID

from sentinex_proxy import gen_ca


def _cn(confdir) -> str:
    data = (confdir / "mitmproxy-ca-cert.pem").read_bytes()
    cert = x509.load_pem_x509_certificate(data)
    return cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value


def test_ca_cn_is_not_mitmproxy(tmp_path, monkeypatch):
    monkeypatch.setenv("MITMPROXY_CONFDIR", str(tmp_path))
    monkeypatch.delenv("SENTINEX_PROXY_CA_CN", raising=False)
    gen_ca.main()
    assert _cn(tmp_path) == gen_ca.DEFAULT_CN
    assert _cn(tmp_path) != "mitmproxy"


def test_ca_cn_override_via_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MITMPROXY_CONFDIR", str(tmp_path))
    monkeypatch.setenv("SENTINEX_PROXY_CA_CN", "Custom CA")
    gen_ca.main()
    assert _cn(tmp_path) == "Custom CA"


def test_second_call_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("MITMPROXY_CONFDIR", str(tmp_path))
    gen_ca.main()
    ca_file = tmp_path / "mitmproxy-ca.pem"
    before = os.path.getmtime(ca_file)
    gen_ca.main()
    after = os.path.getmtime(ca_file)
    assert before == after
