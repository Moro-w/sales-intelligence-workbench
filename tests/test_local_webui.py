"""Offline safety tests for the launcher; not model/business acceptance."""
import importlib.util
import json
import os
from pathlib import Path
import socket
import stat
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('local_webui', Path(__file__).resolve().parents[1] / 'scripts/local_webui.py')
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


class LauncherSafetyTests(unittest.TestCase):
    def test_environment_does_not_inherit_provider_keys_or_old_paths(self):
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'sentinel', 'AIONUI_DATA_DIR': '/old/data', 'AIONUI_DUMP_PROMPTS': '1'}):
            env = launcher.isolated_env(Path('/isolated/project'))
        self.assertNotIn('DEEPSEEK_API_KEY', env)
        self.assertNotIn('AIONUI_DUMP_PROMPTS', env)
        self.assertEqual(env['AIONUI_DATA_DIR'], '/isolated/project/.runtime/aionui')

    def test_home_and_runtime_are_project_local_and_remote_is_disabled(self):
        env = launcher.isolated_env(Path('/isolated/project'))
        self.assertEqual(env['HOME'], '/isolated/project/.runtime/home')
        self.assertEqual(env['AIONUI_ALLOW_REMOTE'], '0')
        self.assertEqual(env['AIONUI_LOG_LEVEL'], 'info')

    def test_meeting_service_configuration_is_project_local_not_inherited(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.runtime').mkdir()
            launcher.private_json(root / '.runtime/meeting-service.json', {'token': 'a' * 64})
            with patch.dict(os.environ, {'AIONUI_MEETING_TOKEN': 'wrong-project', 'AIONUI_MEETING_PORT': '9999'}):
                env = launcher.isolated_env(root)
            self.assertEqual(env['AIONUI_MEETING_TOKEN'], 'a' * 64)
            self.assertEqual(env['AIONUI_MEETING_PORT'], '8049')

    def test_invalid_meeting_configuration_fails_without_printing_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.runtime').mkdir()
            launcher.private_json(root / '.runtime/meeting-service.json', {'token': 'do-not-print'})
            with self.assertRaises(RuntimeError) as error:
                launcher.isolated_env(root)
            self.assertNotIn('do-not-print', str(error.exception))

    def test_private_json_is_persistent_and_owner_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'access.json'
            launcher.private_json(path, {'test': 'content'})
            self.assertEqual(json.loads(path.read_text()), {'test': 'content'})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertFalse(path.with_suffix('.tmp').exists())

    def test_occupied_port_is_rejected_without_stopping_it(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            with self.assertRaisesRegex(RuntimeError, 'occupied'):
                launcher.ensure_free_port(listener.getsockname()[1])
            self.assertGreater(listener.fileno(), 0)

    def test_missing_process_is_not_owned(self):
        with patch.object(launcher, 'process_identity', return_value=''):
            self.assertFalse(launcher.owned_process({'pid': 123, 'identity': ''}))

    def test_reused_pid_is_not_owned(self):
        with patch.object(launcher, 'process_identity', return_value='different-start-time old-project'):
            self.assertFalse(launcher.owned_process({'pid': 123, 'identity': 'original-start-time'}))

    def test_stop_does_not_kill_unowned_process(self):
        with patch.object(launcher, 'read_state', return_value={'pid': 123}), patch.object(launcher, 'owned_process', return_value=False), patch.object(os, 'kill') as kill:
            launcher.stop()
            kill.assert_not_called()

    def test_existing_database_without_access_file_is_not_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            (runtime / 'aionui').mkdir()
            (runtime / 'aionui/aionui-backend.db').touch()
            with patch.object(launcher, 'RUNTIME', runtime), patch.object(launcher.subprocess, 'run') as run:
                with self.assertRaisesRegex(RuntimeError, 'refusing to reset'):
                    launcher.bootstrap({})
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
