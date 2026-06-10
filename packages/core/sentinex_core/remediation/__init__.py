"""Remediation playbooks, auto-generated patches, and the diff applier."""

from .patcher import PatchError, apply_unified_diff
from .templates import build_remediation

__all__ = ["build_remediation", "apply_unified_diff", "PatchError"]
