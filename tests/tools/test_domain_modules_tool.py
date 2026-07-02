import json

import pytest

from tools import domain_modules_tool as dm


MODULE = """---
kind: domain-module
name: local-inference-domain-module
description: Background context for local inference.
tags: [local-llm, inference]
---

# Local Inference

Dense background context, not a procedure.
"""


@pytest.fixture(autouse=True)
def isolated_domain_modules(tmp_path, monkeypatch):
    root = tmp_path / "domain-modules"
    monkeypatch.setattr(dm, "DOMAIN_MODULES_DIR", root)
    yield root


def test_domain_module_manage_create_list_view_and_support_file():
    created = json.loads(dm.domain_module_manage("create", "local-inference-domain-module", content=MODULE))
    assert created["success"] is True

    listed = json.loads(dm.domain_modules_list())
    assert listed["count"] == 1
    assert listed["modules"][0]["name"] == "local-inference-domain-module"

    viewed = json.loads(dm.domain_module_view("local-inference-domain-module"))
    assert viewed["success"] is True
    assert viewed["kind"] == "domain-module"
    assert "Dense background context" in viewed["content"]
    assert "not skills" in viewed["usage_hint"]

    wrote = json.loads(
        dm.domain_module_manage(
            "write_file",
            "local-inference-domain-module",
            file_path="references/hardware.md",
            file_content="# Hardware notes",
        )
    )
    assert wrote["success"] is True
    support = json.loads(dm.domain_module_view("local-inference-domain-module", "references/hardware.md"))
    assert support["content"] == "# Hardware notes"


def test_domain_module_requires_kind_frontmatter():
    bad = MODULE.replace("kind: domain-module", "kind: skill")
    result = json.loads(dm.domain_module_manage("create", "local-inference-domain-module", content=bad))
    assert result["success"] is False
    assert "kind: domain-module" in result["error"]


@pytest.mark.parametrize("path", ["../secret.md", "scripts/run.py", "/tmp/x", "references/../secret.md"])
def test_support_files_are_path_constrained(path):
    assert json.loads(dm.domain_module_manage("create", "local-inference-domain-module", content=MODULE))["success"]
    result = json.loads(
        dm.domain_module_manage(
            "write_file",
            "local-inference-domain-module",
            file_path=path,
            file_content="nope",
        )
    )
    assert result["success"] is False


def test_build_context_uses_background_not_skill_instruction_language():
    assert json.loads(dm.domain_module_manage("create", "local-inference-domain-module", content=MODULE))["success"]
    context = dm.build_domain_module_context(["local-inference-domain-module"])
    assert "task-scoped background context" in context
    assert "follow its instructions" not in context
    assert "Dense background context" in context
