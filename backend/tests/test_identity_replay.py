import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.scripts.reprocess_identity import reprocess
from backend.app.core.exceptions import ResultNotFound
from backend.app.models.model_settings import ModelSettings
from backend.app.repositories.result_repository import ResultRepository
from backend.app.agents.sonar_agent import SonarAgent
from backend.tests.test_evidence_agent import sonar_fixture,candidate_answer


class IdentityReplayTests(unittest.TestCase):
    def test_replay_never_calls_model_keeps_source_and_writes_a_separate_result(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            root=Path(temp);context,images=sonar_fixture();context['require_spatial']=True
            paths=[root/'btr_A_hull.png',root/'btr_A_tow.png']
            for path,data in zip(paths,images):path.write_bytes(data)
            workflow=SonarAgent();prompt=workflow.prepare(paths,'test','images',context)
            repo=ResultRepository(root/'outputs');source=repo.create_run()
            workflow.persist_evidence(source);answer=candidate_answer(prompt)
            repo.save(source,'answer.txt',answer)
            repo.save(source,'response.json',{'usage':{'input_tokens':100,'output_tokens':200}})
            with (patch('backend.app.infrastructure.llm.client.send_images',side_effect=AssertionError('must not call provider')),
                  patch('backend.app.infrastructure.llm.stream_client.stream_images',side_effect=AssertionError('must not call provider'))):
                result=reprocess(source.name,root/'outputs')
            self.assertNotEqual(result['result_id'],source.name)
            self.assertEqual(result['new_model_calls'],0);self.assertTrue(result['trajectory_generated'])
            self.assertEqual((source/'answer.txt').read_text(encoding='utf-8'),answer)
            replay=root/'outputs'/result['result_id']
            metadata=json.loads((replay/'execution_report.json').read_text(encoding='utf-8'))
            self.assertTrue(metadata['offline_reprocessed'])
            self.assertEqual(metadata['source_result_id'],source.name)
            self.assertTrue((replay/'identity_report.json').is_file())
            with self.assertRaises(ResultNotFound):reprocess('../private',root/'outputs')
