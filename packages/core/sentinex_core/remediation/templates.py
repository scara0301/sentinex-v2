"""
Remediation templates — per-rule playbooks and auto-generated patches.

Each finding gets a markdown playbook with code-level guidance. Where a
machine-applicable fix makes sense, the template also emits a unified diff
that adds a ``sentinex_policy.yaml`` guardrail file to the agent bundle;
``apply_fix`` applies the diff to a copy of the bundle and re-scans it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

_POLICY_HEADER = "# SENTINEX runtime guardrail policy — enforce in your tool dispatcher\n"


def _new_file_diff(path: str, content: str) -> str:
    lines = content.splitlines()
    body = "\n".join(f"+{line}" for line in lines)
    return (
        f"--- /dev/null\n"
        f"+++ b/{path}\n"
        f"@@ -0,0 +1,{len(lines)} @@\n"
        f"{body}\n"
    )


@dataclass(frozen=True)
class RemediationTemplate:
    playbook_md: str
    policy_yaml: Optional[str] = None  # rendered into a sentinex_policy.yaml diff

    def build(self) -> tuple[str, Optional[str]]:
        diff = None
        if self.policy_yaml is not None:
            diff = _new_file_diff(
                "sentinex_policy.yaml", _POLICY_HEADER + self.policy_yaml
            )
        return self.playbook_md, diff


_TEMPLATES: dict[str, RemediationTemplate] = {
    "TOOL-RPP-001": RemediationTemplate(
        playbook_md=(
            "## Return-path poisoning — agent contacted attacker host\n\n"
            "The agent followed instructions embedded in a tool *response* and "
            "called a host it was never asked to call.\n\n"
            "1. Treat all tool output as untrusted data: never feed raw tool "
            "responses back into the prompt without wrapping them in a data "
            "envelope (e.g. fenced JSON the system prompt declares non-executable).\n"
            "2. Enforce an egress allowlist in your tool dispatcher — reject URLs "
            "and hosts that were not present in the user task or tool schema.\n"
            "3. Strip or neutralize imperative phrases (`SYSTEM`, `OVERRIDE`, "
            "`ignore previous instructions`) from tool responses before they "
            "reach the model.\n"
        ),
        policy_yaml=(
            "egress:\n"
            "  mode: allowlist\n"
            "  allowed_hosts:\n"
            "    - api.openai.com\n"
            "    - api.anthropic.com\n"
            "tool_responses:\n"
            "  sanitize_instructions: true\n"
        ),
    ),
    "TOOL-RPP-002": RemediationTemplate(
        playbook_md=(
            "## Return-path poisoning — injected content propagated downstream\n\n"
            "Content injected into a tool response was found verbatim in the "
            "arguments of a later tool call, meaning the model is copying "
            "untrusted data into privileged channels.\n\n"
            "1. Tag tool-derived strings and forbid them from appearing in "
            "tool-call arguments without explicit user confirmation.\n"
            "2. Add an output filter that drops known injection markers before "
            "dispatching tool calls.\n"
        ),
        policy_yaml=(
            "tool_responses:\n"
            "  sanitize_instructions: true\n"
            "  taint_tracking: true\n"
        ),
    ),
    "TOOL-EXFIL-001": RemediationTemplate(
        playbook_md=(
            "## Sensitive data exfiltration via tool call\n\n"
            "Honeypot PII/credentials planted in the sandbox left through an "
            "outbound tool call to a non-LLM host.\n\n"
            "1. Add a DLP filter in front of every outbound tool call: redact "
            "SSNs, card numbers, API keys, and emails unless the destination "
            "is explicitly approved for that data class.\n"
            "2. Scope database credentials so the agent can only read the "
            "tables its task requires.\n"
            "3. Require human confirmation for any tool call that sends more "
            "than N records to an external service.\n"
        ),
        policy_yaml=(
            "dlp:\n"
            "  redact_patterns: [ssn, credit_card, api_key, email]\n"
            "  block_on_match: true\n"
        ),
    ),
    "LLM-LEAK-001": RemediationTemplate(
        playbook_md=(
            "## Sensitive data leaked into LLM prompt stream\n\n"
            "Honeypot data was sent to the model provider as prompt content. "
            "Even when the provider is trusted, this widens the blast radius "
            "of any prompt logging or retention.\n\n"
            "1. Redact or tokenize PII before inserting tool results into the "
            "context window.\n"
            "2. Prefer passing record *identifiers* to the model and resolving "
            "them in tools, instead of inlining full records.\n"
        ),
        policy_yaml=(
            "dlp:\n"
            "  redact_in_prompts: true\n"
            "  redact_patterns: [ssn, credit_card, api_key]\n"
        ),
    ),
    "TOOL-DOW-001": RemediationTemplate(
        playbook_md=(
            "## Denial of wallet — billable API loop\n\n"
            "The agent called a billable API far more often than any "
            "reasonable task requires.\n\n"
            "1. Add a per-tool budget (max calls per run) in your dispatcher "
            "and fail closed when it is exhausted.\n"
            "2. Add loop detection: if the same tool is called with similar "
            "arguments repeatedly, halt and surface the loop to a human.\n"
            "3. Set hard spend limits in the provider dashboard as a backstop.\n"
        ),
        policy_yaml=(
            "rate_limits:\n"
            "  per_tool_max_calls: 25\n"
            "  on_exceeded: halt\n"
        ),
    ),
    "TOOL-DOW-002": RemediationTemplate(
        playbook_md=(
            "## Excessive total tool-call volume\n\n"
            "Total outbound call volume crossed the configured threshold, "
            "which usually indicates a planning loop.\n\n"
            "1. Cap total tool invocations per task and make the agent "
            "summarize-and-stop when the cap is reached.\n"
            "2. Log and review the call chain to find the cycle.\n"
        ),
        policy_yaml=(
            "rate_limits:\n"
            "  total_max_calls: 200\n"
            "  on_exceeded: halt\n"
        ),
    ),
}

_CATEGORY_FALLBACKS: dict[str, str] = {
    "llm_layer": (
        "## LLM-layer finding\n\nHarden the system prompt, separate "
        "instructions from data, and add output validation before acting on "
        "model responses.\n"
    ),
    "tool_layer": (
        "## Tool-layer finding\n\nValidate tool arguments against the tool "
        "schema, treat tool responses as untrusted, and enforce least "
        "privilege per tool.\n"
    ),
    "memory_state": (
        "## Memory/state finding\n\nBound context growth, validate retrieved "
        "documents before they enter the prompt, and namespace per-session "
        "state.\n"
    ),
    "multi_agent": (
        "## Multi-agent finding\n\nAuthenticate inter-agent messages and "
        "restrict delegation depth and capabilities per role.\n"
    ),
    "infrastructure": (
        "## Infrastructure finding\n\nRun the agent with least privilege, "
        "drop capabilities, restrict egress, and never mount secrets into "
        "the agent environment.\n"
    ),
    "business_logic": (
        "## Business-logic finding\n\nEnforce transaction limits, "
        "confirmation gates, and authorization checks outside the model — "
        "in deterministic code.\n"
    ),
}


def build_remediation(rule_id: str, category: str) -> tuple[str, Optional[str]]:
    """Return ``(playbook_md, diff_or_none)`` for a finding."""
    template = _TEMPLATES.get(rule_id)
    if template is not None:
        return template.build()
    fallback = _CATEGORY_FALLBACKS.get(
        category,
        "## Finding\n\nReview the captured evidence and apply least-privilege "
        "controls around the affected tool calls.\n",
    )
    return fallback, None
