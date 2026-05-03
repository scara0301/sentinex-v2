#!/bin/bash
set -euo pipefail
# The agent code is mounted read-only at /work.
# The orchestrator sets HTTPS_PROXY, OPENAI_BASE_URL, ANTHROPIC_BASE_URL, SCAN_ID.
exec python /work/agent_entry.py "$@"
