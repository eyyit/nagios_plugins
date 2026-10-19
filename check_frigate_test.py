"""Fixture tests: never interrupt cameras or modify recordings."""
import copy
import importlib.machinery
import importlib.util
import pathlib
import subprocess
import unittest
from unittest import mock

loader = importlib.machinery.SourceFileLoader('check_frigate', str(pathlib.Path(__file__).with_name('check_frigate')))
spec = importlib.util.spec_from_loader(loader.name, loader)
check = importlib.util.module_from_spec(spec)
loader.exec_module(check)


class FrigateChecks(unittest.TestCase):
    def setUp(self):
        self.camera = {'camera_config': {'enabled': True, 'record_enabled': True}, 'stats': {'camera_fps': 5, 'process_fps': 5}, 'recording_end': 980, 'now': 1000, 'file_exists': True, 'recording_intervals': [[900, 980]], 'missing_recent_files': 0}

    def test_camera_states(self):
        for age, expected in ((20, 0), (120, 0), (121, 1), (300, 1), (301, 2)):
            with self.subTest(age=age):
                self.camera['recording_end'] = 1000 - age
                self.assertEqual(check.evaluate_camera(self.camera, 600, 120, 300)[0], expected)

    def test_low_fps(self):
        self.camera['stats']['process_fps'] = 0
        self.camera['stats']['camera_fps'] = 0
        self.assertEqual(check.evaluate_camera(self.camera, 600, 120, 300)[0], 0)
        del self.camera['stats']
        self.assertEqual(check.evaluate_camera(self.camera, 600, 120, 300)[0], 0)

    def test_recovered_gap_remains_warning_then_expires(self):
        self.camera['recording_intervals'] = [[800, 850], [900, 980]]
        code, text = check.evaluate_camera(self.camera, 600, 120, 300)
        self.assertEqual(code, 1)
        self.assertIn('(50s)', text)
        self.assertIn('1970-01-01T00:14:10+00:00', text)
        self.camera.update(now=1801, recording_end=1800,
                           recording_intervals=[[900, 980], [980, 1800]])
        self.assertEqual(check.evaluate_camera(self.camera, 2000, 120, 300)[0], 0)

    def test_overlap_and_gap_boundaries(self):
        self.assertEqual(check.recording_gaps([[900, 950], [910, 920], [940, 980]], 1000), [])
        self.assertEqual(check.recording_gaps([[800, 850], [879.9, 980]], 1000), [])
        self.assertEqual(check.recording_gaps([[800, 850], [880, 980]], 1000), [(850, 880)])
        self.assertEqual(check.recording_gaps([[0, 50], [140, 980]], 1000), [(100, 140)])
        self.assertEqual(check.recording_gaps([[0, 50], [120, 980]], 1000), [])
        self.assertEqual(check.recording_gaps([[0, 1000]], 1100), [])

    def test_missing_recent_file_is_critical(self):
        self.camera['missing_recent_files'] = 1
        self.assertEqual(check.evaluate_camera(self.camera, 600, 120, 300)[0], 2)

    def test_missing_and_disabled_camera(self):
        for config in (None, {'enabled': False, 'record_enabled': True}, {'enabled': True, 'record_enabled': False}):
            self.camera['camera_config'] = config
            self.assertEqual(check.evaluate_camera(self.camera, 600, 120, 300)[0], 2)

    def test_no_file_and_no_recordings(self):
        self.camera['file_exists'] = False
        self.assertEqual(check.evaluate_camera(self.camera, 600, 120, 300)[0], 2)
        self.camera['recording_end'] = None
        self.assertEqual(check.evaluate_camera(self.camera, 600, 120, 300)[0], 2)

    def test_startup_grace(self):
        self.camera['recording_end'] = None
        self.assertEqual(check.evaluate_camera(self.camera, 100, 120, 300)[0], 0)

    def test_memory_thresholds_and_historical_counters(self):
        for usage, code in ((840, 0), (850, 1), (950, 2)):
            data = {'current': usage + 100, 'limit': 1000, 'stat': {'inactive_file': 100}, 'events': {'max': 42, 'oom': 1, 'oom_kill': 1}}
            result = check.evaluate_memory(data, 85, 95)
            self.assertEqual(result[0], code)
            self.assertIn('oom_kills=1c', result[1])

    def test_invalid_memory(self):
        with self.assertRaises(ValueError):
            check.evaluate_memory({'current': 1, 'limit': 0}, 85, 95)

    def test_unknown_timeout_permission_and_bad_json(self):
        for error in (subprocess.TimeoutExpired(['docker'], 20), subprocess.CalledProcessError(1, ['docker']), FileNotFoundError()):
            with mock.patch.object(check, 'execute', side_effect=error), mock.patch('builtins.print'):
                self.assertEqual(check.main(['memory']), 3)
        with mock.patch.object(check, 'execute', return_value='invalid'), mock.patch('builtins.print'):
            self.assertEqual(check.main(['memory']), 3)

    def test_unknown_unsupported_schema_and_api_failure(self):
        state = '{"Running":true,"StartedAt":"2020-01-01T00:00:00Z"}'
        for diagnostic in ('{}', '{"camera_config":null}'):
            with mock.patch.object(check, 'execute', side_effect=[state, diagnostic]), mock.patch('builtins.print'):
                self.assertEqual(check.main(['memory']), 3)
        with mock.patch.object(check, 'execute', side_effect=[state, subprocess.CalledProcessError(1, ['docker'])]), mock.patch('builtins.print'):
            self.assertEqual(check.main(['camera', 'Doorbell']), 3)

    def test_nonfinite_recording_rejected(self):
        self.camera['recording_intervals'] = [[900, float('nan')]]
        with self.assertRaises(ValueError):
            check.evaluate_camera(self.camera, 600, 120, 300)


if __name__ == '__main__':
    unittest.main()
