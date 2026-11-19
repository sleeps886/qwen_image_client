import ast
import json
from pathlib import Path
import unittest

from backend.app.agents.sonar_agent import SonarAssociationAgent,build_association_prompt
from backend.app.models.model_settings import ModelSettings
from backend.app.agents.sonar_agent import assemble


class SonarAgentTests(unittest.TestCase):
    def test_graph_delegates_prompt_building_to_agent_module(self):
        state={'evidence':{'images':[{'image_index':0,'platform_id':'A','array':'hull','roi':[0,0,100,100],
                                     'candidates':[{'id':'I0R0P0','t':0,'bearing_deg':30}]}]},
               'rules':[],'tracklets':[],'count_hypothesis':{'min':1,'max':2},'trace':[]}
        prompt=build_association_prompt(state['evidence'],state['rules'],state['tracklets'],state['count_hypothesis'])
        self.assertEqual(assemble(state)['prompt'],prompt)
        self.assertIn('"coordinate_system":"candidate_pairs"',prompt)
        self.assertIn('target_inventory',prompt)
        self.assertIn('I0R0P0',prompt)

    def test_sync_agent_single_gateway_call_and_no_data_mode(self):
        calls=[]
        class Gateway:
            def invoke(self,*args):
                calls.append(args);return {'answer':'{}','output_dir':'mock-output'}
        agent=SonarAssociationAgent(Gateway())
        state={'images':['hull.png','tow.png'],'prompt':'test','mode':'images'}
        settings=ModelSettings('fake','unused','private-runtime-key')
        result=agent.invoke_state(state,settings,Path('unused'))
        self.assertEqual(result['answer'],'{}');self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][-1],'images')
        self.assertNotIn('private-runtime-key',json.dumps(state))
        with self.assertRaises(ValueError):agent.invoke_state(state|{'mode':'data'},settings,Path('unused'))
        self.assertEqual(len(calls),1)

    def test_complete_agent_owns_graph_but_does_not_depend_on_http(self):
        path=Path(__file__).parents[1]/'app/agents/sonar_agent.py'
        tree=ast.parse(path.read_text(encoding='utf-8'))
        self.assertIn('SonarAgent',{node.name for node in tree.body if isinstance(node,ast.ClassDef)})
        self.assertIn('ForwardInverseAgent',{node.name for node in tree.body if isinstance(node,ast.ClassDef)})
        self.assertTrue(any(isinstance(node,ast.ImportFrom) and node.module=='langgraph.graph' for node in tree.body))
        for node in ast.walk(tree):
            if isinstance(node,ast.ImportFrom):
                self.assertFalse((node.module or '').startswith(('fastapi','starlette','backend.app.workflows')))


class SonarAgentStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_agent_forwards_events_without_retry(self):
        calls=[]
        class Gateway:
            async def stream(self,*args):
                calls.append(args)
                yield {'event':'delta','text':'{}'}
                yield {'event':'model_complete','result':{'answer':'{}'}}
        agent=SonarAssociationAgent(Gateway())
        events=[e async for e in agent.stream_state({'images':['hull.png'],'prompt':'test','mode':'images'},
                                                   ModelSettings('fake','unused','private'),Path('unused'))]
        self.assertEqual([e['event'] for e in events],['delta','model_complete'])
        self.assertEqual(len(calls),1)
