"""Дополнительные проверки исправлений; преподавательский tests/ не меняется."""
import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from test_eval import report, rows, outputs
from evaluation.solution import quality_gate, score
from src.promote import promote


class StrictScoring(unittest.TestCase):
    def test_duplicate_json_key_rejected(self):
        out = outputs(rows(1))
        out[0]['raw'] = '{"answer":"A","answer":"B"}'
        self.assertEqual(score(rows(1), out)['overall']['valid'], 0)

    def test_generation_error_overrides_valid_json(self):
        out = outputs(rows(1))
        out[0]['error'] = 'timeout'
        self.assertEqual(score(rows(1), out)['overall'], dict(
            n=1, correct=0, valid=0, errors=1, accuracy=0, format_rate=0))

    def test_overlapping_slices_do_not_inflate_overall(self):
        items = rows(2)
        items[0]['slices'] = ['s0', 's1']
        result = score(items, outputs(items))
        self.assertEqual(result['overall']['n'], 2)
        self.assertEqual(result['slices']['s1']['n'], 2)

    def test_duplicate_task_ids_rejected(self):
        with self.assertRaises(ValueError):
            score(rows(1) * 2, outputs(rows(1)))


class StrictGate(unittest.TestCase):
    def test_membership_change_with_same_counts_rejected(self):
        original, changed = report(), report()
        changed['per_item'][0]['slices'], changed['per_item'][1]['slices'] = (
            changed['per_item'][1]['slices'], changed['per_item'][0]['slices'])
        with self.assertRaises(ValueError):
            quality_gate(original, changed, ['s0'])

    def test_missing_behavior_and_empty_critical_rejected(self):
        changed = report()
        del changed['behavioral']
        for value, critical in ((changed, ['s0']), (report(), [])):
            with self.assertRaises(ValueError):
                quality_gate(report(), value, critical)

    def test_forged_slice_counts_rejected(self):
        changed = report()
        changed['slices']['s0']['n'] += 1
        with self.assertRaises(ValueError):
            quality_gate(report(), changed, ['s0'])

    def test_baseline_generation_failure_rejected(self):
        baseline = report()
        baseline['per_item'][0]['error'] = 'timeout'
        with self.assertRaises(ValueError):
            quality_gate(baseline, report(), ['s0'])


class LossGate(unittest.TestCase):
    def test_rejected_loss_never_writes_registry(self):
        for final, base, threshold, status in (
            (2, 1, 1, 'FINISHED'), (1, 1, 0, 'FINISHED'),
            (math.nan, 1, 1, 'FINISHED'), (1, 0, 1, 'FINISHED'),
            (-1, 1, 1, 'FINISHED'),
            (1, 1, 1, 'FAILED'), (1, 1, math.nan, 'FINISHED'),
        ):
            run = SimpleNamespace(data=SimpleNamespace(metrics=dict(final_val_loss=final, base_val_loss=base)),
                                  info=SimpleNamespace(status=status, run_id='fixture'))
            client = Mock()
            with self.subTest(final=final, base=base, threshold=threshold, status=status):
                with patch('src.promote.mlflow.register_model') as register:
                    with self.assertRaises(ValueError):
                        promote(client, run, 'fixture', 'candidate', threshold)
                    register.assert_not_called()
                    self.assertEqual(client.mock_calls, [])

    def test_passing_loss_registers_candidate(self):
        run = SimpleNamespace(data=SimpleNamespace(metrics=dict(final_val_loss=1, base_val_loss=2)),
                              info=SimpleNamespace(status='FINISHED', run_id='fixture'))
        version = SimpleNamespace(version='7')
        client = Mock()
        with patch('src.promote.mlflow.register_model', return_value=version) as register:
            self.assertIs(promote(client, run, 'fixture', 'candidate', 1), version)
            register.assert_called_once_with('runs:/fixture/model', 'fixture')
            client.set_registered_model_alias.assert_called_once_with('fixture', 'candidate', '7')

    def test_direct_champion_bypass_rejected(self):
        run = SimpleNamespace(data=SimpleNamespace(metrics=dict(final_val_loss=1, base_val_loss=2)),
                              info=SimpleNamespace(status='FINISHED', run_id='fixture'))
        with patch('src.promote.mlflow.register_model') as register:
            with self.assertRaises(ValueError):
                promote(Mock(), run, 'fixture', 'champion', 1)
            register.assert_not_called()


if __name__ == '__main__':
    unittest.main()
