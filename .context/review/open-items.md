# Open items — decisions I did not make for you

Everything in `fixes.md` is done and verified. Item 3 has since been closed.
The rest are open either because they need a call that is yours, or because I
could not complete them without deleting committed files.

---

## 1. Two lockfiles for the web app (F-19) — needs a decision

`pnpm-workspace.yaml` claims `apps/web`, and `pnpm-lock.yaml` was committed
deliberately (commit `f95fc7e`, "Add pnpm-lock.yaml for the web workspace").
But `apps/web/Dockerfile` runs `npm ci` against `apps/web/package-lock.json`.

This is not just untidy — it decides whether the build works:

- **npm** produces a flat `node_modules` inside `apps/web`. Turbopack's root is
  `apps/web`, everything resolves, the build succeeds. This is what the
  Dockerfile does and what I verified.
- **pnpm** symlinks `next` out to the repo-root store. Turbopack resolves the
  symlink to a real path outside its root and fails with the same error as
  F-01, which no `next.config.ts` setting fixes.

So a contributor who runs `pnpm install` today gets a broken build even with
F-01 fixed.

**Recommendation:** standardise on npm for `apps/web` — delete
`pnpm-workspace.yaml` and `pnpm-lock.yaml`, keep `apps/web/package-lock.json`.
That matches the shipping Dockerfile and needs no other change.

I did not do this myself: deleting two deliberately committed files is your
call, and the sandbox blocked the removal when I attempted it.

If you would rather keep pnpm, the alternative is to set `turbopack.root` to
the repo root — but the Docker context only contains `apps/web`, so that path
does not exist at image-build time and the config would need to branch on
environment. I would avoid it.

## 2. The integration and e2e suites are empty

```
tests/integration/__init__.py   0 bytes
tests/e2e/__init__.py           0 bytes

$ pytest tests/integration tests/e2e --collect-only
no tests collected
```

`make test-integration` and `make test-e2e` therefore pass without asserting
anything. If either runs in CI, it is currently a green light that means
nothing.

The manual run recorded in `verification.md` is exactly what an e2e test
should automate: upload an agent, start a scan, poll to DONE, assert
`TOOL-RPP-001` fires and the score is non-zero. `conftest.py` already provides
`docker_client`, `test_db_url` and `test_redis_url` fixtures for it. Say the
word and I will write it.

## 3. Sandbox images — CLOSED

All five now build. `crewai` is the heavy one at 1.32 GB; `langchain` 527 MB,
`mcp` 741 MB, `autogen` 340 MB, `raw_python` 299 MB. Only `raw_python` has been
exercised with a real agent; the rest are built but not run.

## 4. A stale TODO about `detect-secrets` (cosmetic)

`_run_detect_secrets` in `apps/api/sentinex_api/routes/agents.py` carries:

```python
# TODO: ensure detect-secrets is installed in the container
```

I checked, and it is:

```
$ docker run --rm --entrypoint sh sentinex/api:latest -c "which detect-secrets && detect-secrets --version"
/usr/local/bin/detect-secrets
1.5.0
```

So the TODO is stale and can be deleted. The one thing worth keeping in mind
is that the fallback path is quiet by design: if the binary ever goes missing,
uploads still report `secret_count: 0` and the reason appears only in the
`warning` field of the response, not as an error.

---

# Things I deliberately did not change

- **The hardcoded demo workspace UUID** is gone from the dashboard, replaced by
  deep-link/localStorage/env resolution. I did not add a login flow — there is
  no auth system beyond workspace API keys, and adding one is a product
  decision, not a bug fix.
- **`apps/mocks` request shapes.** The mock Stripe accepts JSON bodies, while
  real Stripe uses form encoding. That is a realism gap an agent could
  fingerprint, but changing it would alter the mock's contract with any
  existing scenario, so it belongs in a deliberate realism pass.
- **The `PROVIDER_PATTERNS` list.** I widened what gets *recorded* rather than
  extending the provider list, because the detections key on host substrings
  and adding providers would not have fixed the underlying gap.
