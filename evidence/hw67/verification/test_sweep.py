"""Настоящие локальные MLflow runs; обучение заменено короткой фикстурой."""
import tempfile
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import mlflow
from mlflow import MlflowClient

from src.common import load_params
# Изолируем только обучение: проверка MLflow не должна загружать стек модели.
with patch.dict(sys.modules, {'src.train': SimpleNamespace(train=None)}):
    from src import sweep


class SweepIsolation(unittest.TestCase):
    def test_six_independent_runs_keep_data_versions(self):
        params = load_params()
        versions = dict(code_sha256='code', config_sha256='config',
                        data_train_sha256='train', data_val_sha256='val',
                        git_sha='a' * 40, git_dirty='False')
        previous = mlflow.get_tracking_uri()
        with tempfile.TemporaryDirectory() as temp:
            mlflow.set_tracking_uri((Path(temp) / 'mlruns').as_uri())
            mlflow.set_experiment('sweep-regression')

            def fake_training(config, candidate, provenance):
                # Пишем несовместимые между собой параметры: общий run вызвал бы ошибку MLflow.
                mlflow.set_tags(dict(provenance, kind='candidate', candidate=candidate['name']))
                mlflow.log_params(dict(r=candidate['r'], lr=candidate['lr']))
                mlflow.log_metric('final_val_loss', 1.0)
                return dict(run_id=mlflow.active_run().info.run_id, name=candidate['name'])

            try:
                with (patch.object(sweep, 'load_params', return_value=params),
                      patch.object(sweep, 'connect'),
                      patch.object(sweep, 'provenance', return_value=versions),
                      patch.object(sweep, 'run_candidate', side_effect=fake_training),
                      patch.object(sweep, 'save_json') as saved):
                    sweep.main()
                summary = saved.call_args.args[1]
                client = MlflowClient()
                parent = client.get_run(summary['parent_id'])
                children = client.search_runs([parent.info.experiment_id],
                    filter_string=f"tags.mlflow.parentRunId = '{parent.info.run_id}'")
                self.assertEqual(len(children), 6)
                self.assertEqual(len({r.info.run_id for r in children}), 6)
                self.assertEqual(parent.info.status, 'FINISHED')
                self.assertEqual({r.data.tags['candidate'] for r in children},
                                 {c['name'] for c in params['sweep']})
                for run in children:
                    self.assertEqual(run.info.status, 'FINISHED')
                    self.assertEqual(run.data.tags['data_train_sha256'], 'train')
                    self.assertEqual(run.data.tags['data_val_sha256'], 'val')
            finally:
                mlflow.set_tracking_uri(previous)


if __name__ == '__main__':
    unittest.main()
