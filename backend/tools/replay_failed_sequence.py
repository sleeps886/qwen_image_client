"""Copy a stopped run, revalidate its saved reply, continue with a fake gateway.
Never dispatch to user settings or write the original run.
"""
import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from backend.app.services.association import sequential_runner as seq
from backend.app.api.routers.incremental import read_workspace_state
from backend.app.services.association.incremental_prediction import save_state
from backend.app.models.model_settings import ModelSettings


class FakeGateway:
    def __init__(self):self.contexts=[]
    def invoke(self,images,prompt,*args):
        ctx=json.loads(prompt.split('\n',1)[1]);self.contexts.append(ctx)
        return {'answer':json.dumps({'step_id':ctx['step_id'],'base_state_version':ctx['base_state_version'],
                                    'tracks':[],'unassigned':[p['observation_id'] for p in ctx['observations']]})}


def replay(path,rid,output):
    original={name:hashlib.sha256((path.parent/name).read_bytes()).hexdigest() for name in ('state.json','observations.sqlite3')}
    with tempfile.TemporaryDirectory(prefix='replay-failed-queue-') as d:
        q=Path(d)/'state.json'
        for name in original:shutil.copy2(path.parent/name,Path(d)/name)
        before=seq.read_run(q,rid);assert before['status']=='failed','Only a stopped failed run is supported'
        answer=before['steps'][-1]['answer'];calls=before['calls']
        recovered=seq.recover_returned(q,rid,read_workspace_state,save_state)
        assert recovered['calls']==calls
        run=seq.read_run(q,rid);assert run['steps'][-1]['answer']==answer
        # Only this disposable copy is reauthorized for the explicit fake fixture.
        settings=ModelSettings('offline-fake','http://invalid.local','fake')
        run['model_reference']=None;run['spec']['model_fingerprint']=seq.model_reference(settings)
        clean={k:v for k,v in run['spec'].items() if k not in ('context_digest','model_calls')}
        run['spec']['context_digest']=seq.digest(clean);seq.write_run(q,run)
        gateway=FakeGateway()
        for i in range(run['cursor'],len(run['spec']['queue'])):seq.execute_one(q,rid,i,settings,gateway,read_workspace_state,save_state)
        end=seq.read_run(q,rid);view=seq.public_run(end)['working_view'];geo=view['spatial_preview']
        report={'source_session':path.parent.name,'original_validated_units':before['cursor'],
            'original_error':before['steps'][-1]['error'],'free_recovered_cursor':recovered['cursor'],
            'original_answer_unchanged':end['steps'][before['cursor']]['answer']==answer,
            'final_cursor':end['cursor'],'queue_units':len(end['spec']['queue']),
            'fake_continuation_calls':len(gateway.contexts),'new_real_model_calls':0,
            'visible_raw_observations':len(view['points']),'visible_units':len(view['units']),
            'spatial_points_including_mirrors':sum(len(t['points']) for t in geo['tracks']),
            'root_projection_unchanged':read_workspace_state(q)['tracks']==read_workspace_state(path)['tracks'],
            'note':'Continuation fixture explicitly leaves all editable observations unassigned. Completion/display test, not recognition accuracy.'}
    assert original=={name:hashlib.sha256((path.parent/name).read_bytes()).hexdigest() for name in original}
    report['production_files_unchanged']=True
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('state',type=Path);p.add_argument('run_id');p.add_argument('--output',type=Path,default=Path('output/supplied-zip-test/failed-20queue-replay.json'));a=p.parse_args();replay(a.state,a.run_id,a.output)
