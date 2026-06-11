#!/bin/bash
set -euo pipefail
# The agent bundle is mounted read-only at /work. Uploaded archives extract
# to /work/src; a single uploaded .py file also lands there. The orchestrator
# sets HTTPS_PROXY, OPENAI_BASE_URL, ANTHROPIC_BASE_URL, SCAN_ID, and (when the
# proxy CA could be extracted) the CA-trust env vars.
#
# Resolve the entry script: explicit override, then conventional names, then a
# lone .py file in src/ or the bundle root.

resolve_entry() {
    if [ -n "${SENTINEX_ENTRYPOINT:-}" ] && [ -f "${SENTINEX_ENTRYPOINT}" ]; then
        printf '%s\n' "${SENTINEX_ENTRYPOINT}"
        return 0
    fi
    for dir in /work/src /work; do
        for name in agent_entry.py main.py agent.py app.py run.py __main__.py; do
            if [ -f "$dir/$name" ]; then
                printf '%s\n' "$dir/$name"
                return 0
            fi
        done
    done
    # Fall back to a single .py file if the bundle contains exactly one.
    for dir in /work/src /work; do
        if [ -d "$dir" ]; then
            local matches
            matches="$(find "$dir" -maxdepth 1 -name '*.py' 2>/dev/null)"
            if [ "$(printf '%s\n' "$matches" | grep -c .)" = "1" ]; then
                printf '%s\n' "$matches"
                return 0
            fi
        fi
    done
    return 1
}

if ! ENTRY="$(resolve_entry)"; then
    echo "sentinex: no agent entry script found under /work/src or /work" >&2
    echo "sentinex: set SENTINEX_ENTRYPOINT or include agent_entry.py/main.py/agent.py" >&2
    exit 3
fi

echo "sentinex: launching agent entry $ENTRY" >&2
cd "$(dirname "$ENTRY")"
exec python "$ENTRY" "$@"
