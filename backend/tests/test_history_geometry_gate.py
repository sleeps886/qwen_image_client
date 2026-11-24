import copy
import importlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from backend.app.repositories.result_repository import ResultRepository


class HistoryGeometryGateTests(unittest.TestCase):
    def test_legacy_or_rejected_model_image_is_not_successfully_loaded(self):
        module=importlib.import_module('backend.app.api.routers.results')
        for audit,blocked in (({},True),({'gate_passed':False},True),({'gate_passed':True},False)):
            with self.subTest(audit=audit),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);repo=ResultRepository(root);run=repo.create_run()
                repo.save(run,'tracks.json',{'coordinate_system':'local_xy_m','tracks':[],'warnings':[]})
                repo.save(run,'answer.txt','saved model answer')
                repo.save(run,'execution_report.json',{'model_final_requested':True,'model_final':{'status':'generated','geometry_audit':audit}})
                (run/'estimated_track.png').write_bytes(b'PNG')
                with patch.object(module,'OUTPUTS',root):result=module.historical_result(run.name)
                self.assertEqual(result['trajectory_image'] is None,blocked)
                self.assertEqual(result['model_final']['status'],'failed' if blocked else 'generated')
                self.assertEqual('estimated_track.png' not in result['downloads'],blocked)
