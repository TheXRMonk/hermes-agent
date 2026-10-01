"""Approval previews describe the exact outgoing payload before transport."""
import unittest
from unittest.mock import patch
from tools import mcp_tool as core
from tools.mcp_tool_handlers import _make_tool_handler

class ApprovalPreviewTests(unittest.TestCase):
    def test_payload_reaches_consent_before_transport(self):
        args={'issue_key':'MTDB-36','fields':{'summary':'Combined SimGo idea','description':'Keep requirements\nand references'}}
        with patch('tools.mcp_tool_scope._server_key',return_value='test'), patch.dict(core._server_trust_levels,{'test':'untrusted'}), patch('tools.mcp_tool_handlers._tool_is_read_only',return_value=False), patch('tools.approval_prompt.request_elicitation_consent',return_value='decline') as consent, patch('tools.mcp_tool_handlers._acquire_call_server') as acquire:
            result=_make_tool_handler('test','jira_update_issue',30)(args)
            self.assertIn('did not approve',result)
            acquire.assert_not_called()
            text='\n'.join(consent.call_args.args)
            for expected in ['Action: Update Jira issue','Target: MTDB-36','Combined SimGo idea','Keep requirements','references']:
                self.assertIn(expected,text)
            self.assertEqual(args['fields']['summary'],'Combined SimGo idea')

    def test_preview_redacts_secrets_even_when_global_redaction_disabled(self):
        import os
        from tools.mcp_tool_handlers import _mutation_preview
        args = {'password': 'DEMO_ONLY_NOT_A_REAL_SECRET',
                'url': 'https://example.invalid/?api_key=DEMO_QUERY_NOT_A_REAL_SECRET'}
        with patch('agent.redact._redact_enabled', return_value=False):
            preview = _mutation_preview('test', 'update', args)
        self.assertNotIn('DEMO_ONLY_NOT_A_REAL_SECRET', preview)
        self.assertNotIn('DEMO_QUERY_NOT_A_REAL_SECRET', preview)
        self.assertEqual(args['password'], 'DEMO_ONLY_NOT_A_REAL_SECRET')

    def test_move_and_delete_are_explicit(self):
        from tools.mcp_tool_handlers import _mutation_preview
        move = _mutation_preview('atlassian', 'jira_move_issue', {'issue_key':'MTDB-36', 'target_project_key':'MD'})
        self.assertIn('Source: MTDB-36', move)
        self.assertIn('Destination: MD', move)
        delete = _mutation_preview('atlassian', 'jira_delete_issue', {'issue_key':'MTDB-18', 'delete_subtasks':False})
        self.assertIn('Warning: deletes this issue', delete)
        self.assertIn('"delete_subtasks": false', delete)

if __name__ == '__main__':
    unittest.main()
