"""
Pre-generate the mitmproxy CA store with a generic (non-self-describing)
CN/organization before mitmdump boots.

``mitmproxy.certs.CertStore.from_store()`` only generates a fresh CA when
``<confdir>/mitmproxy-ca.pem`` is absent — otherwise it adopts whatever is
already there. By calling ``CertStore.create_store()`` ourselves first, with
a generic ``organization``/``cn`` instead of mitmproxy's default (which is
the literal string ``"mitmproxy"``, a trivial fingerprint for any agent that
inspects the certificate chain of a "third-party" HTTPS call), mitmdump picks
up our CA unmodified. Every derived file mitmdump/the orchestrator expects
(``mitmproxy-ca.pem``, ``mitmproxy-ca-cert.pem``, etc.) is written by
``create_store`` in one call, so nothing downstream needs to change.

Each container instance still gets its own fresh CA — this only skips
regeneration when *this* container has already run it once (guarded by the
existence check below), it does not reuse a CA across scans.
"""
from __future__ import annotations

import os
from pathlib import Path

from mitmproxy.certs import CertStore

DEFAULT_CN = "Web Filtering Proxy"
DEFAULT_ORG = "Network Services"


def main() -> None:
    confdir = Path(
        os.environ.get("MITMPROXY_CONFDIR", os.path.expanduser("~/.mitmproxy"))
    )
    confdir.mkdir(parents=True, exist_ok=True)
    if (confdir / "mitmproxy-ca.pem").exists():
        return  # already generated for this container instance
    CertStore.create_store(
        confdir,
        basename="mitmproxy",
        key_size=2048,
        organization=os.environ.get("SENTINEX_PROXY_CA_ORG", DEFAULT_ORG),
        cn=os.environ.get("SENTINEX_PROXY_CA_CN", DEFAULT_CN),
    )


if __name__ == "__main__":
    main()
