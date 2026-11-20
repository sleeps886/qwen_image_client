import ast
import json
from pathlib import Path
import unittest

from backend.app.agents.tools import SonarAssociationAgent
from backend.app.agents.prompts import build_association_prompt
from backend.app.models.model_settings import ModelSettings
from backend.app.agents.nodes.evidence import assemble


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

    def test_agent_entry_and_nodes_have_separate_ownership(self):
        path=Path(__file__).parents[1]/'app/agents/sonar_agent.py'
        tree=ast.parse(path.read_text(encoding='utf-8'))
        self.assertIn('SonarAgent',{node.name for node in tree.body if isinstance(node,ast.ClassDef)})
        self.assertNotIn('ForwardInverseAgent',{node.name for node in tree.body if isinstance(node,ast.ClassDef)})
        self.assertFalse(any(isinstance(node,ast.ImportFrom) and node.module=='langgraph.graph' for node in tree.body))
        self.assertFalse(any(isinstance(node,ast.FunctionDef) for node in tree.body))
        for node in ast.walk(tree):
            if isinstance(node,ast.ImportFrom):
                self.assertFalse((node.module or '').startswith(('fastapi','starlette','backend.app.workflows')))

    def test_inverse_nodes_and_graph_are_separate_from_agent_lifecycle(self):
        root=Path(__file__).parents[1]/'app/agents'
        node_tree=ast.parse((root/'nodes/inverse.py').read_text(encoding='utf-8'))
        node_class=next(node for node in node_tree.body if isinstance(node,ast.ClassDef))
        self.assertEqual({node.name for node in node_class.body if isinstance(node,ast.FunctionDef)},
                         {'__init__','validate','field','paths_node','associations','fit','refine','review','quality','render','model_final'})
        agent_tree=ast.parse((root/'forward_inverse_agent.py').read_text(encoding='utf-8'))
        run=next(node for node in ast.walk(agent_tree) if isinstance(node,ast.FunctionDef) and node.name=='run')
        self.assertFalse(any(isinstance(node,ast.FunctionDef) for node in run.body))
        graph_tree=ast.parse((root/'graphs/inverse.py').read_text(encoding='utf-8'))
        self.assertIn('build_inverse_graph',{node.name for node in graph_tree.body if isinstance(node,ast.FunctionDef)})


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
