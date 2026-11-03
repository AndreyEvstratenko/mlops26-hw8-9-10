"""Additional final-architecture checks beyond the six before/after regressions."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import lab
import pipeline
from release import read, write, digest, canonical


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'workspace'
        lab.initialize(self.root)

    def test_pointer_journal_crash_reconciled_once(self):
        with self.assertRaises(RuntimeError):
            pipeline.run(self.root, fail_after_pointer=True)
        self.assertEqual(read(self.root / 'accepted.json')['model_id'], 'fixture-B')
        self.assertEqual(pipeline.events(self.root), [])
        pipeline.run(self.root)
        pipeline.run(self.root)
        self.assertEqual(len(pipeline.events(self.root)), 1)

    def test_rollback_uses_previous_valid_report(self):
        pipeline.run(self.root)
        pipeline.run(self.root, 'report-A.json', manifest_name='manifest-A.json')
        self.assertEqual(read(self.root / 'accepted.json')['model_id'], 'fixture-A')
        self.assertEqual(len(pipeline.events(self.root)), 2)

    def test_artifact_tamper_rejected_before_pointer(self):
        before = (self.root / 'accepted.json').read_bytes()
        (self.root / 'model-B.txt').write_text('tampered', encoding='utf-8')
        with self.assertRaises(ValueError):
            pipeline.run(self.root)
        self.assertEqual((self.root / 'accepted.json').read_bytes(), before)

    def test_changed_runtime_and_eval_get_distinct_release_ids(self):
        first = pipeline.run(self.root)['release_id']
        m, r = read(self.root / 'manifest-B.json'), read(self.root / 'report-B.json')
        m['runtime']['dtype'] = 'float16'
        m['runtime_sha256'] = digest(canonical(m['runtime']))
        r.update(runtime_sha256=m['runtime_sha256'], evaluation_run_id='fixture-new-evaluation')
        write(self.root / 'manifest-B.json', m)
        write(self.root / 'report-B.json', r)
        second = pipeline.run(self.root)['release_id']
        self.assertNotEqual(first, second)

    def test_disk_replace_failure_preserves_pointer_and_cleans_temp(self):
        before = (self.root / 'accepted.json').read_bytes()
        with patch('os.replace', side_effect=OSError('simulated replacement failure')):
            with self.assertRaises(OSError):
                import release
                release.publish(read(self.root / 'manifest-B.json'), read(self.root / 'report-B.json'),
                                self.root / 'accepted.json')
        self.assertEqual((self.root / 'accepted.json').read_bytes(), before)
        self.assertFalse(list(self.root.glob('*.tmp')))
