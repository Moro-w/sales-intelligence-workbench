from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import local_meetings as launcher


class MeetingLauncherTests(unittest.TestCase):
    def test_missing_pid_is_never_owned(self):
        with patch.object(launcher, 'process_identity', return_value=''):
            self.assertFalse(launcher.owned({}))

    def test_invalid_pid_does_not_query_processes(self):
        with patch.object(launcher, 'process_identity') as identity:
            for pid in (0, -1, True, None, '10'):
                self.assertFalse(launcher.owned({'pid': pid}))
            identity.assert_not_called()

    def test_reused_pid_is_never_owned(self):
        with patch.object(launcher, 'process_identity', return_value='another-process'):
            self.assertFalse(launcher.owned({'pid': 10, 'identity': 'prior-process'}))

    def test_even_matching_identity_requires_project_python_and_factory(self):
        with patch.object(launcher, 'process_identity', return_value='unrelated-process'):
            self.assertFalse(launcher.owned({'pid': 10, 'identity': 'unrelated-process'}))
        identity = f'start-time {launcher.PYTHON} -m uvicorn app.text_main:create_app --factory'
        with patch.object(launcher, 'process_identity', return_value=identity):
            self.assertTrue(launcher.owned({'pid': 10, 'identity': identity}))

    def test_stop_does_not_touch_unowned_process(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(launcher, 'STATE', Path(tmp) / 'missing'), patch.object(launcher.os, 'kill') as kill:
            launcher.stop()
            kill.assert_not_called()

    def test_start_does_not_reuse_an_occupied_port_or_create_credentials(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(launcher, 'STATE', Path(tmp) / 'missing'), patch.object(launcher, 'ensure_free_port', side_effect=RuntimeError('occupied')), patch.object(launcher, 'private_json') as write, patch.object(launcher.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(RuntimeError, 'occupied'):
                launcher.start()
            write.assert_not_called()
            spawn.assert_not_called()


if __name__ == '__main__':
    unittest.main()
