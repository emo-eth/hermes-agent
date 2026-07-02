import json

from cron.scheduler import _build_job_prompt


MODULE_PAYLOAD = {
    "success": True,
    "name": "local-inference-domain-module",
    "content": "---\nkind: domain-module\nname: local-inference-domain-module\ndescription: ctx\n---\n\n# Local inference context",
}


def test_cron_injects_domain_modules_as_background_context(monkeypatch):
    def fake_view(name):
        assert name == "local-inference-domain-module"
        return json.dumps(MODULE_PAYLOAD)

    monkeypatch.setattr("tools.domain_modules_tool.domain_module_view", fake_view)
    prompt = _build_job_prompt(
        {
            "domain_modules": ["local-inference-domain-module"],
            "prompt": "summarize inference options",
        }
    )

    assert "task-scoped background context" in prompt
    assert "# Local inference context" in prompt
    assert "summarize inference options" in prompt
    assert "follow its instructions" not in prompt


def test_cron_loads_skills_and_domain_modules_with_distinct_semantics(monkeypatch):
    monkeypatch.setattr("tools.domain_modules_tool.domain_module_view", lambda name: json.dumps(MODULE_PAYLOAD))
    monkeypatch.setattr("tools.skills_tool.skill_view", lambda name: json.dumps({"success": True, "content": "# Skill procedure"}))
    monkeypatch.setattr("tools.skill_usage.bump_use", lambda name: None)

    prompt = _build_job_prompt(
        {
            "skills": ["some-skill"],
            "domain_modules": ["local-inference-domain-module"],
            "prompt": "do the thing",
        }
    )

    assert "task-scoped background context" in prompt
    assert "# Local inference context" in prompt
    assert 'invoked the "some-skill" skill' in prompt
    assert "follow its instructions" in prompt
    assert prompt.index("task-scoped background context") < prompt.index('invoked the "some-skill" skill')
