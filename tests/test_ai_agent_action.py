"""Tests for prompt rendering and command building in AIAgentAction."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import AIAgentAction


def _write_prompt(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return str(path)


class TestRenderPromptTemplate:
    def test_renders_supported_variables(self, tmp_path):
        prompt_file = _write_prompt(
            tmp_path,
            "prompt.txt",
            "{filepath}|{filename}|{basename}|{folder}|{ext}",
        )
        filepath = str(tmp_path / "invoice.final.pdf")
        rendered = AIAgentAction.render_prompt_template(prompt_file, filepath)
        assert rendered == f"{filepath}|invoice.final|invoice.final.pdf|{tmp_path}|pdf"

    def test_unknown_variable_raises_value_error(self, tmp_path):
        prompt_file = _write_prompt(tmp_path, "prompt.txt", "{unknown}")
        with pytest.raises(ValueError, match="Unknown variable"):
            AIAgentAction.render_prompt_template(prompt_file, str(tmp_path / "file.txt"))

    def test_unescaped_braces_raise_value_error(self, tmp_path):
        prompt_file = _write_prompt(tmp_path, "prompt.txt", '{"key": "value"}')
        with pytest.raises(ValueError, match="Invalid template"):
            AIAgentAction.render_prompt_template(prompt_file, str(tmp_path / "file.txt"))

    def test_hidden_file_has_no_extension(self, tmp_path):
        prompt_file = _write_prompt(tmp_path, "prompt.txt", "{filename}|{basename}|{ext}")
        rendered = AIAgentAction.render_prompt_template(prompt_file, str(tmp_path / ".env"))
        assert rendered == ".env|.env|"


class TestBuildAgentCommand:
    def test_claude_command_uses_print_mode(self):
        cmd = AIAgentAction.build_agent_command("claude", "hello")
        assert cmd == ["claude", "-p", "hello", "--dangerously-skip-permissions"]

    def test_codex_command_uses_workspace_write(self):
        cmd = AIAgentAction.build_agent_command("codex", "hello")
        assert cmd == ["codex", "exec", "hello", "-s", "workspace-write", "--skip-git-repo-check"]

    def test_gemini_is_explicitly_unverified(self):
        with pytest.raises(ValueError, match="not yet verified"):
            AIAgentAction.build_agent_command("gemini", "hello")

    def test_unknown_model_raises(self):
        with pytest.raises(ValueError, match="Unknown AI agent model"):
            AIAgentAction.build_agent_command("wat", "hello")


class TestResolveExecutable:
    def test_prefers_path_lookup(self, monkeypatch):
        monkeypatch.setattr(AIAgentAction.shutil, "which", lambda name: f"/path/{name}")
        assert AIAgentAction.resolve_executable("codex") == "/path/codex"

    def test_falls_back_to_common_dirs(self, monkeypatch):
        monkeypatch.setattr(AIAgentAction.shutil, "which", lambda name: None)
        monkeypatch.setattr(AIAgentAction, "_COMMON_EXECUTABLE_DIRS", ["/opt/homebrew/bin"])
        monkeypatch.setattr(AIAgentAction.os.path, "isfile", lambda path: path == "/opt/homebrew/bin/codex")
        monkeypatch.setattr(AIAgentAction.os, "access", lambda path, mode: path == "/opt/homebrew/bin/codex")
        assert AIAgentAction.resolve_executable("codex") == "/opt/homebrew/bin/codex"

    def test_returns_none_when_missing(self, monkeypatch):
        monkeypatch.setattr(AIAgentAction.shutil, "which", lambda name: None)
        monkeypatch.setattr(AIAgentAction, "_COMMON_EXECUTABLE_DIRS", ["/opt/homebrew/bin"])
        monkeypatch.setattr(AIAgentAction.os.path, "isfile", lambda path: False)
        monkeypatch.setattr(AIAgentAction.os, "access", lambda path, mode: False)
        assert AIAgentAction.resolve_executable("codex") is None


class TestBuildAgentEnv:
    def test_prepends_common_dirs_to_path(self, monkeypatch):
        monkeypatch.setattr(AIAgentAction, "_COMMON_EXECUTABLE_DIRS", ["/opt/homebrew/bin", "/usr/local/bin"])
        monkeypatch.setattr(AIAgentAction.os, "environ", {"PATH": "/usr/bin:/bin"})
        env = AIAgentAction.build_agent_env()
        assert env["PATH"] == "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

    def test_keeps_existing_entries_unique(self, monkeypatch):
        monkeypatch.setattr(AIAgentAction, "_COMMON_EXECUTABLE_DIRS", ["/opt/homebrew/bin", "/usr/local/bin"])
        monkeypatch.setattr(AIAgentAction.os, "environ", {"PATH": "/opt/homebrew/bin:/usr/bin:/bin"})
        env = AIAgentAction.build_agent_env()
        assert env["PATH"] == "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
