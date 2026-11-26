"""Isolated v2 UI server; temporary ledger and fake gateway only."""
import asyncio
import json
import tempfile
from pathlib import Path
import uvicorn
from backend.app.main import create_app
from backend.app.api.routers import live_tracking as api
from backend.app.models.model_settings import ModelSettings

ROOT = tempfile.TemporaryDirectory(prefix='xiaoguai-live-ui-')
api.LIVE_DB = Path(ROOT.name)/'ledger.sqlite3'
api.OUTPUTS_DIR = Path(ROOT.name)
api.model_settings = lambda: ModelSettings('offline-fake','http://invalid.local','offline-fake')
CALLS = []
MODE = 'valid'


class FakeGateway:
    async def stream(self, images, prompt, settings, output_root, mode):
        context = json.loads(prompt.rsplit('\n',1)[1]); CALLS.append(context['snapshot_id'])
        await asyncio.sleep(.3)
        if MODE == 'disconnect':
            yield {'event':'delta','text':'partial-body'}
            raise RuntimeError('isolated fake upstream disconnect')
        if MODE == 'invalid':
            yield {'event':'delta','text':'{"bad":true}'}
            yield {'event':'model_complete','result':{'answer':'{"bad":true}'}}
            return
        bins={}
        for row in context['rows']:
            bins.setdefault((row[2],row[3],row[4]>90),[]).append(row)
        if context['purpose']=='association':
            groups=[{'group_ref':'G'+str(i+1),'point_indices':[p[0] for p in rows],'reason_code':'tentative'} for i,rows in enumerate(bins.values())]
            value={'format':'association-compact-v2','snapshot_id':context['snapshot_id'],
                   'base_state_revision':context['base_state_revision'],'stream_groups':groups,
                   'target_hypotheses':[],'unresolved_groups':[]}
        else:
            curves=[]
            for rows in bins.values():
                if len({r[1] for r in rows})<3:continue
                t=context['forecast_origin_time']+1 if context['purpose']=='causal_forecast' else rows[-1][1]
                curves.append({'support_indices':[r[0] for r in rows],'reason_code':'tentative','samples':[{'event_time':t,'bearing_deg':rows[-1][4],'uncertainty_deg':2}]})
            value={'format':'bearing-prediction-v1','snapshot_id':context['snapshot_id'],'purpose':context['purpose'],'curves':curves}
        answer=json.dumps(value)
        for start in range(0,len(answer),24):
            yield {'event':'delta','text':answer[start:start+24]}; await asyncio.sleep(.03)
        yield {'event':'model_complete','result':{'answer':answer}}


app=create_app()
app.dependency_overrides[api.live_gateway]=lambda:FakeGateway()


@app.get('/api/test/live-fake')
def marker():return {'fake_gateway':True,'calls':len(CALLS)}


@app.post('/api/test/live-mode/{mode}')
def fake_mode(mode:str):
    global MODE
    if mode not in ('valid','invalid','disconnect'):raise ValueError('fake mode')
    MODE=mode
    return {'fake_gateway':True,'mode':MODE}


if __name__=='__main__':
    uvicorn.run(app,host='127.0.0.1',port=8892,log_level='warning')
