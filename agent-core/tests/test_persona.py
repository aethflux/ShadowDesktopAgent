"""Tests for the persona builder.

Covers the three things that can break a user-visible persona swap:
- The default config (empty JSON) renders to the original Hoshino tone.
- A preset round-trips through ``persona_config_json`` and shows up in the
  rendered prompt.
- Each agent role gets the right addendum so terminal/desktop guidance
  doesn't bleed into the companion turn (and vice versa).
"""
from __future__ import annotations

import json

import pytest

from app.config import settings
from app.schemas import PersonaConfig
from app.services.persona import PersonaBuilder, builder


@pytest.fixture(autouse=True)
def restore_persona(monkeypatch):
    """Snapshot ``persona_config_json`` and reset between tests so cross-test
    state can't leak through the module-level ``builder`` singleton."""
    original = settings.persona_config_json
    monkeypatch.setattr(settings, "persona_config_json", original)
    yield
    monkeypatch.setattr(settings, "persona_config_json", original)


def test_default_config_renders_swordswoman_tone(monkeypatch) -> None:
    """An empty persona JSON string must produce the original 星野 tone."""
    monkeypatch.setattr(settings, "persona_config_json", "")
    rendered = builder.render("companion-agent")

    assert "星野" in rendered
    assert "温柔" in rendered
    assert "称呼用户为「你」" in rendered
    # Companion-agent role addendum should be present, terminal's should not.
    assert "记忆用户的偏好" in rendered
    assert "PowerShell" not in rendered


def test_preset_swap_changes_name_and_tone(monkeypatch) -> None:
    """Applying the 学姐 preset must change the rendered name + style."""
    presets = builder.list_presets()
    senpai = next(p for p in presets if p.id == "study_senpai")
    monkeypatch.setattr(
        settings, "persona_config_json", senpai.config.model_dump_json(),
    )
    rendered = builder.render("companion-agent")

    assert "学姐" in rendered
    assert "知识密度高" in rendered
    assert "称呼用户为「同学」" in rendered
    # Hoshino-specific bits must be gone after the swap.
    assert "星野" not in rendered


def test_role_addenda_are_role_scoped(monkeypatch) -> None:
    """Same persona config but different role ⇒ different rendered prompt."""
    monkeypatch.setattr(settings, "persona_config_json", "")
    companion = builder.render("companion-agent")
    desktop = builder.render("desktop-agent")
    terminal = builder.render("terminal-agent")
    observation = builder.render_for_observation()

    assert "记忆用户的偏好" in companion
    assert "屏幕" in desktop
    assert "PowerShell" in terminal
    assert "JSON" in observation  # observation role demands strict JSON output


def test_invalid_json_falls_back_to_defaults(monkeypatch) -> None:
    monkeypatch.setattr(settings, "persona_config_json", "not-json}")
    rendered = builder.render("companion-agent")
    assert "星野" in rendered  # didn't crash, returned defaults


def test_invalid_payload_shape_falls_back_to_defaults(monkeypatch) -> None:
    """A JSON payload that is *not* an object (e.g. a list) is invalid for a
    PersonaConfig and must fall back to defaults rather than raising."""
    monkeypatch.setattr(settings, "persona_config_json", json.dumps(["not", "a", "config"]))
    rendered = builder.render("companion-agent")
    assert "星野" in rendered


def test_custom_system_prompt_is_appended(monkeypatch) -> None:
    """The escape-hatch ``custom_system_prompt`` field must appear verbatim."""
    config = PersonaConfig(custom_system_prompt="禁止讨论今晚的菜单。")
    monkeypatch.setattr(settings, "persona_config_json", config.model_dump_json())
    rendered = builder.render("companion-agent")
    assert "禁止讨论今晚的菜单。" in rendered


def test_emoji_and_length_choices_drive_instructions(monkeypatch) -> None:
    """Choosing 'frequent' emoji + 'concise' length must surface in the prompt."""
    config = PersonaConfig(
        emoji_usage="frequent",
        response_length="concise",
    )
    monkeypatch.setattr(settings, "persona_config_json", config.model_dump_json())
    rendered = builder.render("companion-agent")
    assert "较多使用 emoji" in rendered
    assert "保持简短" in rendered


def test_list_presets_returns_six_archetypes() -> None:
    """The UI relies on having a stable preset catalogue. If we add or remove
    presets, this test should be updated intentionally — not silently."""
    presets = builder.list_presets()
    ids = {p.id for p in presets}
    assert ids == {
        "swordswoman_partner",
        "study_senpai",
        "genki_kouhai",
        "butler",
        "cyber_ai",
        "gentle_onee",
    }


def test_get_preset_unknown_returns_none() -> None:
    assert builder.get_preset("nonexistent") is None


def test_builder_reads_settings_per_call(monkeypatch) -> None:
    """Calling render twice must reflect changes between calls — confirms no
    stale caching that would defeat live persona swaps."""
    fresh_builder = PersonaBuilder()
    monkeypatch.setattr(settings, "persona_config_json", "")
    first = fresh_builder.render("companion-agent")
    assert "星野" in first

    new_config = PersonaConfig(name="测试人格", personality_traits=["试验"])
    monkeypatch.setattr(settings, "persona_config_json", new_config.model_dump_json())
    second = fresh_builder.render("companion-agent")
    assert "测试人格" in second
    assert "星野" not in second
