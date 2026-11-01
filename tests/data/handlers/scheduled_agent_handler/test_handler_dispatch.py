"""Core handler() dispatch concern of src/data/handlers/scheduled_agent_handler.py (rec-2709 Wave 11).

Covers TestHandler: due/not-due, PAT-missing, api-error, prompt-missing, write-fail,
model-override, retired-bedrock-fallthrough, default-missing-provider, retired-provider
short-circuit, missing-pat. Split from tests/test_scheduled_agent_handler.py (VERBATIM move).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import src.data.handlers.scheduled_agent_handler as handler_mod
from src.data.handlers.scheduled_agent_handler import handler


class TestHandler:
    """Tests for handler()."""

    @pytest.fixture(autouse=True)
    def _enable_agents(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SCHEDULED_AGENTS_ENABLED", "true")

    def _make_agent(
        self,
        name: str = "doc-freshness",
        cron: str = "0 6 * * 1",
        provider: str = "github-models",
    ) -> dict:
        return {
            "name": name,
            "cron": cron,
            "model": "gpt-4.1-mini",
            "prompt_path": ".github/prompts/scheduled/test.prompt.md",
            "provider": provider,
        }

    def test_counts_failed_agent_when_pat_unavailable(self) -> None:
        """Missing PAT fails github-models agent without global early return."""
        agents = [self._make_agent(provider="github-models")]
        with (
            patch.object(handler_mod, "_get_github_pat", return_value=""),
            patch.object(handler_mod, "_load_manifest", return_value=agents),
            patch(
                "scripts.run_scheduled_agent.is_agent_due",
                return_value=True,
            ),
        ):
            result = handler({}, None)
        assert result["agents_run"] == 0
        assert result["agents_failed"] == 1

    def test_runs_due_agents_and_writes_findings(self) -> None:
        fake_response = {"choices": [{"message": {"content": ('[{"title": "stale doc", "file": "ARCH.md"}]')}}]}
        agents = [self._make_agent()]

        with (
            patch.object(handler_mod, "_get_github_pat", return_value="ghp_test"),
            patch.object(handler_mod, "_load_manifest", return_value=agents),
            patch.object(handler_mod, "_load_prompt", return_value="test prompt"),
            patch(
                "scripts.run_scheduled_agent.is_agent_due",
                return_value=True,
            ),
            patch(
                "scripts.llm.github_models_client.chat_completion",
                return_value=fake_response,
            ),
            patch(
                "scripts.s3_log_store.write_timestamped_findings",
                return_value="agents/doc-freshness/ts.jsonl",
            ),
        ):
            result = handler({}, None)

        assert result["agents_run"] == 1
        assert result["agents_failed"] == 0
        assert result["total_findings"] == 1
        assert "agents/doc-freshness/ts.jsonl" in result["keys_written"]

    def test_skips_agent_when_not_due(self) -> None:
        agents = [self._make_agent()]

        with (
            patch.object(handler_mod, "_get_github_pat", return_value="ghp_test"),
            patch.object(handler_mod, "_load_manifest", return_value=agents),
            patch(
                "scripts.run_scheduled_agent.is_agent_due",
                return_value=False,
            ),
        ):
            result = handler({}, None)

        assert result["agents_run"] == 0
        assert result["agents_failed"] == 0

    def test_counts_failed_agent_on_api_error(self) -> None:
        agents = [self._make_agent()]

        with (
            patch.object(handler_mod, "_get_github_pat", return_value="ghp_test"),
            patch.object(handler_mod, "_load_manifest", return_value=agents),
            patch.object(handler_mod, "_load_prompt", return_value="test prompt"),
            patch(
                "scripts.run_scheduled_agent.is_agent_due",
                return_value=True,
            ),
            patch(
                "scripts.llm.github_models_client.chat_completion",
                return_value={"error": True, "message": "Rate limit"},
            ),
        ):
            result = handler({}, None)

        assert result["agents_run"] == 0
        assert result["agents_failed"] == 1

    def test_counts_failed_agent_when_prompt_missing(self) -> None:
        agents = [self._make_agent()]

        with (
            patch.object(handler_mod, "_get_github_pat", return_value="ghp_test"),
            patch.object(handler_mod, "_load_manifest", return_value=agents),
            patch.object(handler_mod, "_load_prompt", return_value=""),
            patch(
                "scripts.run_scheduled_agent.is_agent_due",
                return_value=True,
            ),
        ):
            result = handler({}, None)

        assert result["agents_run"] == 0
        assert result["agents_failed"] == 1

    def test_counts_failed_agent_when_write_fails(self) -> None:
        agents = [self._make_agent()]
        fake_response = {"choices": [{"message": {"content": "[]"}}]}

        with (
            patch.object(handler_mod, "_get_github_pat", return_value="ghp_test"),
            patch.object(handler_mod, "_load_manifest", return_value=agents),
            patch.object(handler_mod, "_load_prompt", return_value="prompt"),
            patch(
                "scripts.run_scheduled_agent.is_agent_due",
                return_value=True,
            ),
            patch(
                "scripts.llm.github_models_client.chat_completion",
                return_value=fake_response,
            ),
            patch(
                "scripts.s3_log_store.write_timestamped_findings",
                return_value="",
            ),
        ):
            result = handler({}, None)

        assert result["agents_failed"] == 1

    def test_model_override_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SCHEDULED_AGENT_MODEL", "gemini-3.0-flash")
        agents = [self._make_agent()]
        fake_response = {"choices": [{"message": {"content": "[]"}}]}
        captured_calls: list[dict] = []

        def mock_chat(**kwargs: object) -> dict:
            captured_calls.append(dict(kwargs))
            return fake_response

        with (
            patch.object(handler_mod, "_get_github_pat", return_value="ghp_test"),
            patch.object(handler_mod, "_load_manifest", return_value=agents),
            patch.object(handler_mod, "_load_prompt", return_value="prompt"),
            patch(
                "scripts.run_scheduled_agent.is_agent_due",
                return_value=True,
            ),
            patch(
                "scripts.llm.github_models_client.chat_completion",
                side_effect=mock_chat,
            ),
            patch(
                "scripts.s3_log_store.write_timestamped_findings",
                return_value="agents/x/ts.jsonl",
            ),
        ):
            handler({}, None)

        assert captured_calls[0]["model"] == "gemini-3.0-flash"

    def test_retired_bedrock_provider_falls_through_to_default_branch(self) -> None:
        """provider: bedrock has no branch (CD.28); it hits the github-models default."""
        agents = [
            self._make_agent(
                name="code-smell",
                provider="bedrock",
            )
        ]
        fake_response = {"choices": [{"message": {"content": "[]"}}]}

        with (
            patch.object(handler_mod, "_get_github_pat", return_value="ghp_test") as mock_pat,
            patch.object(handler_mod, "_load_manifest", return_value=agents),
            patch.object(handler_mod, "_load_prompt", return_value="prompt"),
            patch(
                "scripts.run_scheduled_agent.is_agent_due",
                return_value=True,
            ),
            patch(
                "scripts.llm.github_models_client.chat_completion",
                return_value=fake_response,
            ) as mock_gh,
            patch(
                "scripts.s3_log_store.write_timestamped_findings",
                return_value="agents/code-smell/ts.jsonl",
            ),
        ):
            result = handler({}, None)

        # No Bedrock branch remains: the agent transits the default
        # (github-models) branch, which requires the PAT.
        mock_pat.assert_called_once()
        mock_gh.assert_called_once()
        assert result["agents_run"] == 1

    def test_default_missing_provider_falls_back_to_github_models(
        self,
    ) -> None:
        """Agent without a provider field defaults to github-models."""
        agent = {
            "name": "legacy-agent",
            "cron": "0 6 * * 1",
            "model": "gpt-4.1-mini",
            "prompt_path": ".github/prompts/scheduled/test.prompt.md",
            # No "provider" key -- must default to github-models
        }
        fake_response = {"choices": [{"message": {"content": "[]"}}]}

        with (
            patch.object(handler_mod, "_get_github_pat", return_value="ghp_test"),
            patch.object(handler_mod, "_load_manifest", return_value=[agent]),
            patch.object(handler_mod, "_load_prompt", return_value="prompt"),
            patch(
                "scripts.run_scheduled_agent.is_agent_due",
                return_value=True,
            ),
            patch(
                "scripts.llm.github_models_client.chat_completion",
                return_value=fake_response,
            ) as mock_gh,
            patch(
                "scripts.s3_log_store.write_timestamped_findings",
                return_value="agents/legacy-agent/ts.jsonl",
            ),
        ):
            result = handler({}, None)

        # Should have called github-models, not bedrock
        mock_gh.assert_called_once()
        assert result["agents_run"] == 1

    def test_retired_provider_agent_short_circuits_before_pat_lookup(self) -> None:
        """A retired-provider agent fails without consuming the shared PAT lookup."""
        sdk_agent = self._make_agent(name="sdk-agent", provider="copilot-sdk")
        sdk_agent["model"] = "claude-haiku-4.5"
        gh_agent = self._make_agent(name="gh-agent", provider="github-models")
        agents = [sdk_agent, gh_agent]

        fake_gh_response = {"choices": [{"message": {"content": "[]"}}]}

        with (
            patch.object(handler_mod, "_get_github_pat", return_value="ghp_test") as mock_pat,
            patch.object(handler_mod, "_load_manifest", return_value=agents),
            patch.object(handler_mod, "_load_prompt", return_value="prompt"),
            patch(
                "scripts.run_scheduled_agent.is_agent_due",
                return_value=True,
            ),
            patch(
                "scripts.llm.github_models_client.chat_completion",
                return_value=fake_gh_response,
            ),
            patch(
                "scripts.s3_log_store.write_timestamped_findings",
                return_value="agents/x/ts.jsonl",
            ),
        ):
            result = handler({}, None)

        # Only the github-models agent needs the PAT; the retired-provider
        # agent fails before reaching the shared PAT lookup.
        mock_pat.assert_called_once()
        assert result["agents_run"] == 1
        assert result["agents_failed"] == 1

    def test_missing_pat_fails_github_models_only(self) -> None:
        """Without a PAT, the github-models agent fails; the retired-provider agent already failed."""
        sdk_agent = self._make_agent(name="sdk-agent", provider="copilot-sdk")
        sdk_agent["model"] = "claude-haiku-4.5"
        gh_agent = self._make_agent(name="gh-agent", provider="github-models")
        agents = [sdk_agent, gh_agent]

        with (
            patch.object(handler_mod, "_get_github_pat", return_value="") as mock_pat,
            patch.object(handler_mod, "_load_manifest", return_value=agents),
            patch.object(handler_mod, "_load_prompt", return_value="prompt"),
            patch(
                "scripts.run_scheduled_agent.is_agent_due",
                return_value=True,
            ),
            patch(
                "scripts.llm.github_models_client.chat_completion",
            ) as mock_gh,
            patch(
                "scripts.s3_log_store.write_timestamped_findings",
                return_value="agents/x/ts.jsonl",
            ),
        ):
            result = handler({}, None)

        mock_pat.assert_called_once()
        mock_gh.assert_not_called()
        assert result["agents_run"] == 0
        assert result["agents_failed"] == 2


class TestHandlerGating:
    """Disabled short-circuit and force_agent selection of handler()."""

    def test_disabled_flag_short_circuits_before_loading_the_manifest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SCHEDULED_AGENTS_ENABLED", "false")
        with patch.object(handler_mod, "_load_manifest") as load_manifest:
            result = handler({}, None)
        assert result["status"] == "disabled"
        load_manifest.assert_not_called()

    def test_force_agent_bypasses_the_cron_check(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SCHEDULED_AGENTS_ENABLED", "true")
        agents = [{"name": "forced", "enabled": False}, {"name": "other", "enabled": False}]
        with (
            patch.object(handler_mod, "_load_manifest", return_value=agents),
            patch("scripts.run_scheduled_agent.is_agent_due") as is_due,
        ):
            result = handler({"force_agent": "forced"}, None)
        is_due.assert_not_called()
        assert result["agents_run"] == 0
        assert result["agents_failed"] == 0
