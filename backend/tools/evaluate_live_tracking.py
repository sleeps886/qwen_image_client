"""Reproducible blind-input engineering benchmark, no real/fake cloud calls.

Run: python -m backend.tools.evaluate_live_tracking --output output/live-evaluation.json
No target labels are sent into the production evidence path.
"""
import argparse
import json
import random
import tempfile
import time
from pathlib import Path
from backend.app.models.live_tracking import PrepareLive
from backend.app.repositories.live_observation_repository import LiveRepository
from backend.app.services.association.live_tracking import prepare_live


def scenarios():
    result={}
    for scene in ('separated','crossing','gap','turn','array_delay','single_array','sparse_random'):
        rows=[]
        for target in range(2):
            for array in ('hull','tow'):
                if scene=='single_array' and array=='tow':continue
                for t in range(16):
                    if scene=='gap' and 5<=t<=9:continue
                    if scene=='sparse_random' and random.Random(target*100+t*3+(array=='tow')).random()<.5:continue
                    angle=25+target*80+t*.4
                    if scene=='crossing':angle=40+t*4 if target==0 else 120-t*4
                    if scene=='turn':angle=30+target*70+abs(t-8)*2
                    rows.append({'source_observation_id':f'p-{len(rows)}','event_time':t+(.2 if scene=='array_delay' and array=='tow' else 0),
                                 'platform_id':'A','array':array,'bearing_deg':angle+(1 if array=='tow' else 0)})
        result[scene]=rows
    return result


def signature(snapshot):
    lookup={p['observation_id']:(p['event_time'],p['platform_id'],p['array'],p['bearing_deg']) for p in snapshot['points']}
    return sorted(sorted(lookup[x] for x in f['observation_ids']) for f in snapshot['evidence']['fragments'])


def benchmark():
    records=[]
    with tempfile.TemporaryDirectory(prefix='live-evaluation-') as root:
        repo=LiveRepository(Path(root)/'ledger.db')
        for scene, rows in scenarios().items():
            baseline=None
            for seed,size in ((0,1),(19,7),(37,len(rows))):
                ordered=rows.copy();random.Random(seed).shuffle(ordered)
                state=repo.create('scenario-clock',scene,{})
                sid=state['session_id'];start=time.perf_counter()
                for i in range(0,len(ordered),size):
                    repo.ingest(sid,{'source_id':'blind-sensor','timeline_id':'scenario-clock','idempotency_key':str(i),'observations':ordered[i:i+size]})
                ingested=time.perf_counter()
                snapshot=prepare_live(repo,sid,PrepareLive().model_dump());finished=time.perf_counter()
                current=signature(snapshot)
                if baseline is None:baseline=current
                retained=len(snapshot['points'])==len(rows)
                deterministic=current==baseline
                if not retained or not deterministic:raise AssertionError((scene,seed,retained,deterministic))
                records.append({'scene':scene,'arrival_seed':seed,'batch_size':size,'points':len(rows),
                                'all_measurements_retained':retained,'same_measurement_candidate_support':deterministic,
                                'ingestion_seconds':round(ingested-start,4),'evidence_seconds':round(finished-ingested,4),
                                'candidate_coverage':snapshot['evidence']['coverage'],
                                'conflict_sets':len(snapshot['evidence']['conflict_sets']),
                                'strict_sync_groups':snapshot['observability']['strict_synchronous_groups'],
                                'model_calls':0})
    return {'format':'live-offline-evaluation-v1','records':records,'real_model_calls':0,
            'scope':'工程留存、候选支持复现和耗时；不是模型身份识别准确率评估',
            'unverified':['真实模型误合并/误拆分/身份切换率','异步空间解算','硬件流量SLA','误差与不确定性标定']}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='output/live-evaluation.json');args=parser.parse_args()
    result=benchmark();output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f"PASS: {len(result['records'])} scenario/arrival cases; all data retained; deterministic candidates; 0 model calls")
