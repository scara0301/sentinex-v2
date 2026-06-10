import pytest

from sentinex_core.findings import honeypots
from sentinex_core.remediation import PatchError, apply_unified_diff, build_remediation
from sentinex_core.scenarios import builtin_specs


def test_every_builtin_rule_has_specific_template():
    for spec in builtin_specs():
        for det in spec.detections:
            playbook, diff = build_remediation(det.rule_id, det.category)
            assert playbook.startswith("##"), det.rule_id
            # Builtin rules all ship a machine-applicable policy patch.
            assert diff is not None, det.rule_id
            assert "sentinex_policy.yaml" in diff


def test_unknown_rule_falls_back_to_category_playbook():
    playbook, diff = build_remediation("NOPE-999", "business_logic")
    assert "Business-logic" in playbook
    assert diff is None


def test_template_diff_applies_cleanly(tmp_path):
    _, diff = build_remediation("TOOL-EXFIL-001", "tool_layer")
    changed = apply_unified_diff(tmp_path, diff)
    assert changed == ["sentinex_policy.yaml"]
    content = (tmp_path / "sentinex_policy.yaml").read_text()
    assert "dlp:" in content


def test_patch_refuses_to_overwrite_existing_file(tmp_path):
    _, diff = build_remediation("TOOL-DOW-001", "business_logic")
    apply_unified_diff(tmp_path, diff)
    with pytest.raises(PatchError, match="already exists"):
        apply_unified_diff(tmp_path, diff)


def test_patch_modifies_existing_file(tmp_path):
    target = tmp_path / "agent.py"
    target.write_text("import os\n\nAPI_KEY = os.environ['KEY']\nprint(API_KEY)\n")
    diff = """--- a/agent.py
+++ b/agent.py
@@ -1,4 +1,4 @@
 import os

 API_KEY = os.environ['KEY']
-print(API_KEY)
+print("redacted")
"""
    apply_unified_diff(tmp_path, diff)
    assert 'print("redacted")' in target.read_text()
    assert "print(API_KEY)" not in target.read_text()


def test_patch_rejects_context_mismatch(tmp_path):
    (tmp_path / "agent.py").write_text("something else entirely\n")
    diff = """--- a/agent.py
+++ b/agent.py
@@ -1,1 +1,1 @@
-not what is there
+replacement
"""
    with pytest.raises(PatchError):
        apply_unified_diff(tmp_path, diff)


def test_patch_rejects_path_escape(tmp_path):
    diff = """--- /dev/null
+++ b/../escape.txt
@@ -0,0 +1,1 @@
+evil
"""
    with pytest.raises(PatchError, match="escapes"):
        apply_unified_diff(tmp_path, diff)


def test_honeypot_values_match_mocks_package():
    """Core detection values must equal what the mocks actually plant."""
    from sentinex_mocks.common import honeypots as mock_honeypots

    assert set(honeypots.ALL) == set(mock_honeypots.ALL)
