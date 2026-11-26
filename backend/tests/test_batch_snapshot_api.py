from pathlib import Path
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.api.routers import incremental as api
from backend.app.models.model_settings import ModelSettings
from backend.tests.test_batch_snapshot import dataset


def test_budget_consent_and_pinned_batch_schema(tmp_path,monkeypatch):
    monkeypatch.setattr(api,'MIXED_ROOT',tmp_path)
    monkeypatch.setattr(api,'model_settings',lambda:ModelSettings('fake','http://invalid.local','fake'))
    with TestClient(app,base_url='http://127.0.0.1') as client:
        created=client.post('/api/incremental/mixed-sessions',json={'dataset':dataset([10,20]),'max_calls':20})
        assert created.status_code==200
        sid=created.json()['session_id'];base=f'/api/incremental/mixed-sessions/{sid}'
        assert client.post(base+'/call-budget',json={'version':0,'max_calls':500}).status_code==400
        budget=client.post(base+'/call-budget',json={'version':0,'max_calls':500,'consent':True})
        assert budget.status_code==200 and budget.json()['model_calls']==0 and budget.json()['calls']==0
        assert client.post(base+'/call-budget',json={'version':0,'max_calls':600,'consent':True}).status_code==400
        body={'version':budget.json()['version'],'units':[['hull0','tow0'],['hull1','tow1']],
              'execution_mode':'batch-snapshot-v1','batch_size':50,'concurrency':50,'call_limit':3}
        preview=client.post(base+'/runs/preview',json=body)
        assert preview.status_code==200 and preview.json()['model_call_upper_bound']==3
        run=client.post(base+'/runs',json={**body,'context_digest':preview.json()['context_digest'],'request_key':'batch-api'})
        assert run.status_code==200
        assert client.post(base+'/call-budget',json={'version':run.json()['root_version'],'max_calls':800,'consent':True}).status_code==400
        assert client.get(base+f'/runs/{run.json()["id"]}/snapshots').json()['snapshots']==[]
        assert client.get(base).json()['max_calls']==500
