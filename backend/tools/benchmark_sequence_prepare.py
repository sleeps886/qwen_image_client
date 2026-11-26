"""Isolated HTTP queue-preparation benchmark; never dispatches a model."""
import argparse
import json
import subprocess
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.api.routers import incremental as api
from backend.app.services.inputs.observation_import import import_observations
from backend.app.models.model_settings import ModelSettings
from backend.app.core.paths import PROJECT_ROOT


def benchmark(files,output):
    data=import_observations([(p.name,p.read_bytes()) for p in files])
    js="const fs=require('fs'),p=require('./frontend/point-sampling.js');const d=JSON.parse(fs.readFileSync(0,'utf8'));console.log(JSON.stringify(p.groupMarkers(p.sampleSourcePairs(d.candidates,25,d.target_catalog,{random:()=>.5}),d.target_catalog).map(m=>m.observations.map(p=>p.candidate_id))));"
    units=json.loads(subprocess.run(['node','-e',js],cwd=PROJECT_ROOT,input=json.dumps(data),text=True,encoding='utf-8',capture_output=True,check=True).stdout)
    samples=[]
    with tempfile.TemporaryDirectory(prefix='queue-prepare-bench-') as root,patch.object(api,'MIXED_ROOT',Path(root)),patch.object(api,'model_settings',return_value=ModelSettings('offline','http://invalid.local','fake')),TestClient(app,base_url='http://127.0.0.1') as client:
        for i in range(3):
            t=time.perf_counter();r=client.post('/api/incremental/mixed-sessions',json={'dataset':data,'max_calls':25,'persistent_input':True,'timeline_id':'prepare-benchmark-'+str(i)})
            create_ms=(time.perf_counter()-t)*1000;assert r.status_code==200,r.text
            state=r.json();url=f'/api/incremental/mixed-sessions/{state["session_id"]}/runs/preview'
            t=time.perf_counter();bad=client.post(url,json={'version':state['version'],'units':units,'call_limit':20});bad_ms=(time.perf_counter()-t)*1000
            assert bad.status_code==400,bad.text
            t=time.perf_counter();good=client.post(url,json={'version':state['version'],'units':units,'call_limit':24});preview_ms=(time.perf_counter()-t)*1000
            assert good.status_code==200,good.text
            assert good.json()['model_call_upper_bound']==24
            final=client.get(url.removesuffix('/runs/preview')).json();assert final['calls']==0
            samples.append({'create_ms':round(create_ms,2),'insufficient_budget_preview_ms':round(bad_ms,2),'valid_preview_ms':round(preview_ms,2)})
    result={'files':len(files),'raw_observations':len(data['candidates']),'selected_units':len(units),'call_upper_bound':24,'samples':samples,'model_calls':0,'note':'In-process isolated HTTP timing, excludes browser/network/storage latency; not a production SLA.'}
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('files',nargs='+',type=Path);p.add_argument('--output',type=Path,default=Path('output/supplied-zip-test/queue-prepare-benchmark.json'));a=p.parse_args();benchmark(a.files,a.output)
