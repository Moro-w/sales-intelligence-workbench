import importlib.util
import http.client
from http.server import HTTPServer
import json
import threading
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('gate', Path(__file__).resolve().parents[1] / 'scripts/office_budget_gate.py')
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'ledger.json'
        self.budget = gate.Budget(self.path)
        self.body = {'model': gate.MODEL, 'messages': [{'role': 'user', 'content': 'private synthetic input'}], 'stream': True}

    def test_output_cap_and_non_thinking(self):
        row, payload = self.budget.reserve(dict(self.body, max_tokens=99999, max_completion_tokens=99999))
        parsed = json.loads(payload)
        self.assertEqual(parsed['max_tokens'], 1000)
        self.assertNotIn('max_completion_tokens', parsed)
        self.assertEqual(parsed['thinking'], {'type': 'disabled'})
        self.assertEqual(parsed['stream_options'], {'include_usage': True})
        self.assertEqual(row['charged_output'], 1000)

    def test_twelve_requests_including_failures_and_restart(self):
        for _ in range(12):
            row, _ = self.budget.reserve(self.body)
            self.budget.settle(row, None, 'network_failure', False)
        restarted = gate.Budget(self.path)
        with self.assertRaisesRegex(gate.Rejected, 'request limit'):
            restarted.reserve(self.body)
        self.assertEqual(len(restarted.state['requests']), 12)

    def test_input_budget_rejects_before_reservation(self):
        with self.assertRaisesRegex(gate.Rejected, 'budget'):
            self.budget.reserve(dict(self.body, messages=[{'role': 'user', 'content': 'x'*150000}]))
        self.assertEqual(len(self.budget.state['requests']), 0)

    def test_money_budget_independent(self):
        with patch.object(gate, 'MAX_MICROYUAN', 1):
            with self.assertRaisesRegex(gate.Rejected, 'budget'):
                self.budget.reserve(self.body)

    def test_usage_refund_only_when_completed(self):
        row, _ = self.budget.reserve(self.body)
        original = row['charged_input']
        self.budget.settle(row, {'prompt_tokens': 100, 'completion_tokens': 10}, 'broken', False)
        self.assertEqual(row['charged_input'], original)
        self.budget.settle(row, {'prompt_tokens': 100, 'completion_tokens': 10}, 'ok', True)
        self.assertEqual(self.budget.totals(), (100, 10, 280))

    def test_over_bound_usage_halts(self):
        row, _ = self.budget.reserve(self.body)
        self.budget.settle(row, {'prompt_tokens': 999999, 'completion_tokens': 10}, 'ok', True)
        self.assertTrue(self.budget.state['halted'])
        with self.assertRaises(gate.Rejected):
            self.budget.reserve(self.body)

    def test_secrets_not_in_ledger(self):
        self.budget.reserve(self.body)
        self.assertNotIn('private synthetic input', self.path.read_text())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_closed_ledger_cannot_be_reused(self):
        self.budget.state['halted'] = True
        gate.save_private(self.path, self.budget.state)
        with self.assertRaises(gate.Rejected):
            gate.Budget(self.path).reserve(self.body)

    def test_invalid_usage_does_not_release_reservation(self):
        row, _ = self.budget.reserve(self.body)
        original = self.budget.totals()
        self.budget.settle(row, {'prompt_tokens': -1, 'completion_tokens': 0}, 'ok', True)
        self.assertEqual(self.budget.totals(), original)

    def test_http_rejects_without_network(self):
        handler = gate.make_handler({'local_token': 'test-local', 'upstream_key': 'never-forward-this'}, self.budget)
        server = HTTPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(gate.urllib.request, 'build_opener') as upstream:
                for path, token, body, expected in [
                    ('/v1/chat/completions', '', self.body, 401),
                    ('/v1/chat/completions', 'wrong', self.body, 401),
                    ('/other', 'test-local', self.body, 404),
                    ('/v1/chat/completions', 'test-local', dict(self.body, model='other'), 400),
                ]:
                    conn = http.client.HTTPConnection(*server.server_address, timeout=3)
                    conn.request('POST', path, json.dumps(body), {'Authorization': 'Bearer ' + token})
                    resp = conn.getresponse()
                    self.assertEqual(resp.status, expected)
                    self.assertNotIn('never-forward-this', resp.read().decode())
                    conn.close()
                upstream.assert_not_called()
                self.assertEqual(self.budget.state['requests'], [])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)

    def test_redirects_do_not_forward_credentials(self):
        self.assertIsNone(gate.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.example/'))

    def test_model_and_multimodal_denied(self):
        with self.assertRaises(gate.Rejected):
            self.budget.reserve(dict(self.body, model='different-model'))
        with self.assertRaises(gate.Rejected):
            self.budget.reserve(dict(self.body, messages=[{'role':'user','content':[{'type':'image_url','image_url':{'url':'https://example.com'}}]}]))


if __name__ == '__main__':
    unittest.main()
