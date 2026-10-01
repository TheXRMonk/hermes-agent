"""Regression: real SDK annotations must survive discovery into consent gating."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from mcp.types import Tool, ToolAnnotations
from tools import mcp_tool as core
from tools.mcp_tool_registration import _annotation_read_only_hint, _record_tool_trust_metadata
from tools.mcp_tool_handlers import _trust_gate_check


class AnnotationCompatibilityTests(unittest.TestCase):
    def test_real_sdk_read_discovery_bypasses_consent(self):
        tool = Tool(name='jira_search', inputSchema={}, annotations=ToolAnnotations(readOnlyHint=True))
        with patch.dict(core._server_trust_levels, {}, clear=True), patch.dict(core._tool_read_only_hints, {}, clear=True), patch('tools.mcp_tool_scope._server_key', return_value='test'), patch('tools.mcp_tool_scope._resolve_server_key', return_value='test'):
            _record_tool_trust_metadata('test', {'trust': 'untrusted'}, [tool], key='test')
            with patch('tools.approval_prompt.request_elicitation_consent', return_value='decline') as consent:
                self.assertIsNone(_trust_gate_check('test', 'jira_search'))
                consent.assert_not_called()

    def test_legacy_cache_and_unknown_annotations(self):
        for value in [True, False, None, 'true', 1]:
            for annotations in [dict(readOnlyHint=value), SimpleNamespace(readOnlyHint=value), SimpleNamespace(read_only_hint=value)]:
                with self.subTest(value=value, annotations=annotations):
                    self.assertEqual(_annotation_read_only_hint(SimpleNamespace(annotations=annotations)), value is True)
        self.assertFalse(_annotation_read_only_hint(SimpleNamespace(annotations=None)))

    def test_real_sdk_mutations_require_each_consent(self):
        for name in ['jira_create_issue', 'jira_update_issue', 'jira_delete_issue', 'jira_move_issue']:
            for annotation in [ToolAnnotations(readOnlyHint=False), None]:
                tool = Tool(name=name, inputSchema={}, annotations=annotation)
                with patch.dict(core._server_trust_levels, {}, clear=True), patch.dict(core._tool_read_only_hints, {}, clear=True), patch('tools.mcp_tool_scope._server_key', return_value='test'), patch('tools.mcp_tool_scope._resolve_server_key', return_value='test'):
                    _record_tool_trust_metadata('test', {'trust': 'untrusted'}, [tool], key='test')
                    for answer in ['decline', 'cancel', 'accept']:
                        with patch('tools.approval_prompt.request_elicitation_consent', return_value=answer) as consent:
                            result = _trust_gate_check('test', name)
                            self.assertEqual(result is None, answer == 'accept')
                            consent.assert_called_once()

if __name__ == '__main__':
    unittest.main()
