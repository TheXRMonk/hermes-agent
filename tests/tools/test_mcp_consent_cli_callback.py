"""MCP consent must use the owning CLI approval panel, once per call."""
import unittest
from unittest.mock import Mock, patch
from tools.approval_prompt import request_elicitation_consent

class ConsentRoutingTests(unittest.TestCase):
    def test_registered_cli_callback_receives_once_only_request(self):
        callback = Mock(return_value='once')
        with patch('tools.approval_context._is_gateway_approval_context', return_value=False), patch('tools.terminal_tool._get_approval_callback', return_value=callback), patch('tools.approval_prompt._read_choice', side_effect=AssertionError('Must not use stdin')):
            self.assertEqual(request_elicitation_consent('test mutation', 'test only'), 'accept')
        callback.assert_called_once_with('test mutation', 'test only', allow_permanent=False, allow_session=False, title='Confirm this action?')

if __name__ == '__main__':
    unittest.main()
