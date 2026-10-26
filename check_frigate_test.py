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
    def test_quality_thresholds_and_recovery(self):
        for statuses, expected in ((['ok','ok','ok'],0), (['bad','ok','ok'],0),
                                   (['bad','bad','ok'],1), (['bad','bad','bad'],2),
                                   (['timeout','ok','ok'],3)):
            with self.subTest(statuses=statuses):
                samples=[{'end':990-i*30,'status':status} for i,status in enumerate(statuses)]
                self.assertEqual(check.evaluate_quality(samples,1000)[0],expected)

    def test_quality_needs_spaced_recent_samples(self):
        for ends in ([990,989,988],[990,960],[800,770,740]):
            samples=[{'end':end,'status':'bad'} for end in ends]
            self.assertEqual(check.evaluate_quality(samples,1000)[0],0)

    def test_quality_integrates_with_recording_and_cannot_hide_outage(self):
        self.camera['quality']=[{'end':990-i*30,'status':'bad'} for i in range(3)]
        code,text=check.evaluate_camera(self.camera,600,120,300)
        self.assertEqual(code,2)
        self.assertIn('quality_bad=3/3',text)
        self.camera['quality'][0]['status']='ok'
        self.assertEqual(check.evaluate_camera(self.camera,600,120,300)[0],2)
        self.camera['quality']=[{'end':990,'status':'timeout'}]
        self.camera['recording_end']=0
        self.assertEqual(check.evaluate_camera(self.camera,600,120,300)[0],2)

    def test_quality_rejects_unknown_status(self):
        with self.assertRaises(ValueError):
            check.evaluate_quality([{'end':990,'status':'secret'}],1000)

    def setUp(self):
        self.camera = {'camera_config': {'enabled': True, 'record_enabled': True}, 'stats': {'camera_fps': 5, 'process_fps': 5}, 'recording_end': 980, 'now': 1000, 'file_exists': True, 'recording_intervals': [[900, 980]], 'missing_recent_files': 0}

    def test_camera_states(self):
        for age, expected in ((20, 0), (120, 0), (121, 2), (300, 2), (301, 2)):
            with self.subTest(age=age):
                self.camera['recording_end'] = 1000 - age
                self.assertEqual(check.evaluate_camera(self.camera, 600, 120, 300)[0], expected)

    def test_low_fps(self):
        self.camera['stats']['process_fps'] = 0
        self.camera['stats']['camera_fps'] = 0
        self.assertEqual(check.evaluate_camera(self.camera, 600, 120, 300)[0], 0)
        del self.camera['stats']
        self.assertEqual(check.evaluate_camera(self.camera, 600, 120, 300)[0], 0)

    def test_single_recovered_gap_is_informational_then_expires(self):
        self.camera['recording_intervals'] = [[800, 850], [900, 980]]
        code, text = check.evaluate_camera(self.camera, 600, 120, 300)
        self.assertEqual(code, 0)
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


class AlertPolicyTests(unittest.TestCase):
    def test_hourly_gap_count_and_loss_budgets(self):
        data = dict(camera_config=dict(enabled=True, record_enabled=True), now=4000,
                    recording_end=3990, file_exists=True, missing_recent_files=0,
                    recording_intervals=[[1000,1100],[1130,2000],[2030,3000],[3030,3990]])
        self.assertEqual(check.evaluate_camera(data,600,120,300)[0],2)
        data['recording_intervals']=[[1000,1100],[1279,3990]]
        self.assertEqual(check.evaluate_camera(data,600,120,300)[0],0)
        data['recording_intervals']=[[1000,1100],[1280,3990]]
        self.assertEqual(check.evaluate_camera(data,600,120,300)[0],2)
        data['recording_intervals']=[[0,100],[1000,3990]]
        self.assertEqual(check.evaluate_camera(data,600,120,300)[0],2)
        data.update(now=8000,recording_end=7990,recording_intervals=[[0,100],[1000,7990]])
        self.assertEqual(check.evaluate_camera(data,600,120,300)[0],0)

    def test_timing_fault_is_not_audio_corruption(self):
        namespace={}
        exec(check.AUDIO_CLASSIFIER+check.AUDIO_DETAILS,namespace)
        classify=namespace['audio_details']
        self.assertEqual(classify([dict(codec_type='video',start_time='0',duration='5.1'),
                                   dict(codec_type='audio',start_time='0.045',duration='15.9')]),('ok','bad'))
        self.assertEqual(classify([dict(codec_type='video',start_time='0',duration='10'),
                                   dict(codec_type='audio',start_time='0',duration='0.021')]),('bad','ok'))
        self.assertEqual(classify([dict(codec_type='video',start_time='0',duration='10'),
                                   dict(codec_type='audio',start_time='43893',duration='10')]),('ok','bad'))

    def test_sustained_timing_fault_still_alerts(self):
        data=dict(camera_config=dict(enabled=True,record_enabled=True),now=1000,
                  recording_end=990,file_exists=True,missing_recent_files=0,
                  recording_intervals=[[900,990]],audio_expected=True,
                  timing_quality=[dict(end=990-i*30,status='bad') for i in range(3)])
        code,message=check.evaluate_camera(data,600,120,300)
        self.assertEqual(code,2)
        self.assertIn('av_timing_bad=3/3',message)


class AudioClassifierTests(unittest.TestCase):
    def setUp(self):
        namespace = {}
        exec(check.AUDIO_CLASSIFIER, namespace)
        self.classify = namespace['audio_status']
        self.streams = [dict(codec_type='video', start_time='0', duration='10'),
                        dict(codec_type='audio', start_time='0.02', duration='10')]

    def test_aligned_audio_and_silence_are_healthy(self):
        self.assertEqual(self.classify(self.streams), 'ok')
        self.streams[1]['start_time'] = '-0.02'
        self.assertEqual(self.classify(self.streams), 'ok')

    def test_observed_garage_offset_is_bad(self):
        self.streams[1].update(start_time='43893.710000', duration='0.031104')
        self.assertEqual(self.classify(self.streams), 'bad')

    def test_missing_audio_and_truncated_audio_are_bad(self):
        self.assertEqual(self.classify(self.streams[:1]), 'bad')
        self.streams[1]['duration'] = '0.03'
        self.assertEqual(self.classify(self.streams), 'bad')

    def test_skew_boundary_and_excessive_tail(self):
        self.streams[1].update(start_time='2', duration='8')
        self.assertEqual(self.classify(self.streams), 'ok')
        self.streams[1]['start_time'] = '2.001'
        self.assertEqual(self.classify(self.streams), 'bad')
        self.streams[1].update(start_time='0', duration='13')
        self.assertEqual(self.classify(self.streams), 'bad')

    def test_invalid_metadata_is_unknown_not_healthy(self):
        for value in ('N/A', 'nan', 'inf', None):
            self.streams[1]['duration'] = value
            self.assertEqual(self.classify(self.streams), 'unavailable')

    def test_audio_failures_reach_camera_check(self):
        data = dict(camera_config=dict(enabled=True, record_enabled=True), now=1000,
                    recording_end=990, file_exists=True, recording_intervals=[[900,990]],
                    missing_recent_files=0, quality=[], audio_expected=True)
        for statuses, expected in ((['bad','bad','bad'],2), (['bad','bad','ok'],2),
                                   (['ok','ok','ok'],0), (['timeout','ok','ok'],3)):
            data['audio_quality'] = [dict(end=990-i*30, status=s) for i,s in enumerate(statuses)]
            code, message = check.evaluate_camera(data, 600, 120, 300)
            self.assertEqual(code, expected)
            self.assertIn('audio_bad=', message)
        data['audio_expected'] = False
        self.assertEqual(check.evaluate_camera(data, 600, 120, 300)[0], 0)
        data['quality'] = [dict(end=990, status='timeout')]
        self.assertEqual(check.evaluate_camera(data, 600, 120, 300)[0], 3)
        data.update(recording_end=500, audio_expected=True)
        self.assertEqual(check.evaluate_camera(data, 600, 120, 300)[0], 2)


class QualityClassifierTests(unittest.TestCase):
    def test_timestamp_only_requires_successful_decode(self):
        namespace = {}
        exec(check.QUALITY_CLASSIFIER, namespace)
        classify = namespace['quality_status']
        warning = '[null @ 0x624243347000] Application provided invalid, non monotonically increasing dts to muxer in stream 0: 244 >= 244'
        self.assertEqual(classify(0, warning, 300), 'ok')
        self.assertEqual(classify(0, warning+'\n    Last message repeated 3 times', 300), 'ok')
        self.assertEqual(classify(0, '', 300), 'ok')
        for code, text, frames in ((1, warning, 300), (0, warning, 0),
                                   (0, warning+'\n[h264 @ 0xabc] no frame!', 300),
                                   (0, 'unknown diagnostic', 300),
                                   (0, 'Last message repeated 3 times', 300),
                                   (0, warning+'\n[h264 @ 0xabc] no frame!\nLast message repeated 3 times', 300)):
            self.assertEqual(classify(code, text, frames), 'bad')


if __name__ == '__main__':
    unittest.main()
