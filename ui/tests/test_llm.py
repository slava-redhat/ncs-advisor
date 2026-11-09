import os
from pathlib import Path
from tempfile import NamedTemporaryFile
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from advisor.llm import (
    _CopilotChat,
    _format_messages,
    _selected_provider,
    get_llm,
)
from advisor.models import RouterDecision


class CopilotRuntimeCacheTests(unittest.TestCase):
    def test_runtime_cache_directory_is_writable(self):
        cache_dir = Path(os.environ.get(
            "COPILOT_CLI_EXTRACT_DIR", Path.home() / ".cache"))
        cache_dir.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=cache_dir):
            pass


class ProviderSelectionTests(unittest.TestCase):
    def tearDown(self):
        get_llm.cache_clear()

    def test_missing_vertex_project_selects_copilot(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_selected_provider(), "copilot")

    def test_vertex_project_selects_vertex_by_default(self):
        with patch.dict(os.environ, {"ANTHROPIC_VERTEX_PROJECT_ID": "project"}, clear=True):
            self.assertEqual(_selected_provider(), "vertex")

    def test_explicit_copilot_selection_overrides_vertex_project(self):
        env = {
            "NCS_LLM_PROVIDER": "copilot",
            "ANTHROPIC_VERTEX_PROJECT_ID": "project",
            "COPILOT_GITHUB_TOKEN": "test-token",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertIsInstance(get_llm(), _CopilotChat)

    def test_copilot_requires_container_token(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "COPILOT_GITHUB_TOKEN"):
                get_llm()


class CopilotChatTests(unittest.TestCase):
    def test_messages_keep_roles_and_order(self):
        prompt = _format_messages([
            SystemMessage(content="system"),
            HumanMessage(content="question"),
            AIMessage(content="answer"),
        ])
        self.assertIn("System:\nsystem", prompt)
        self.assertIn("User:\nquestion", prompt)
        self.assertIn("Assistant:\nanswer", prompt)
        self.assertLess(prompt.index("question"), prompt.index("answer"))

    def test_structured_output_parses_router_decision(self):
        chat = _CopilotChat()
        content = (
            '{"symptoms":"pod crash","enough_context":true,'
            '"version":"25.7","issue_type":"troubleshoot"}'
        )
        with patch("advisor.llm._copilot_complete_sync", return_value=content):
            result = chat.with_structured_output(RouterDecision).invoke([
                HumanMessage(content="A pod crashes"),
            ])
        self.assertIsInstance(result, RouterDecision)
        self.assertEqual(result.symptoms, "pod crash")


if __name__ == "__main__":
    unittest.main()
