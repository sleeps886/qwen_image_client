"""Bounded real Agent replay. Explicit --run-model required; never retries a failed call.

Run from project root: python -m backend.tools.debug_single_track RESULT_ID --run-model
The offline ridge is a test fixture, NOT independent identity ground truth.
"""
import argparse
import json
import statistics
from pathlib import Path

from fastapi.testclient import TestClient
from backend.app.main import app


def forecast_error(prediction, point):
    for a,b in zip(prediction,prediction[1:]):
        if a['t']<=point['t']<=b['t']:
            bearing=a['bearing_deg']+(b['bearing_deg']-a['bearing_deg'])*(point['t']-a['t'])/(b['t']-a['t'])
            return abs(bearing-point['bearing_deg'])
    for p in prediction:
        if p['t']==point['t']:return abs(p['bearing_deg']-point['bearing_deg'])
    return None


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('result_id');parser.add_argument('--run-model',action='store_true')
    parser.add_argument('--rounds',type=int,choices=range(1,7),default=6)
    parser.add_argument('--start-points',type=int,choices=range(1,6),default=1)
    args=parser.parse_args()
    out=Path('work/single_track_debug');out.mkdir(parents=True,exist_ok=True)
    report={'source_result':args.result_id,'identity_truth':False,'coordinate_system':'bearing_time',
            'uses_full_run_ridge_for_fixture_selection':True,'automatic_retry':False,'rounds':[]}
    with TestClient(app,base_url='http://127.0.0.1') as client:
        response=client.get(f'/api/incremental/batches/{args.result_id}/debug-track')
        response.raise_for_status();batch=response.json();points=batch['candidates']
        (out/'dataset.json').write_text(json.dumps(batch,ensure_ascii=False,indent=2),encoding='utf-8')
        if not args.run_model:
            print('Fixture exported; no model invoked. Pass --run-model for up to 6 paid calls.');return
        response=client.post('/api/incremental/sessions',json={'dataset':batch,'seed_ids':[p['candidate_id'] for p in points[:args.start_points]],
            'stride':1,'horizon':20,'max_calls':args.rounds})
        response.raise_for_status();state=response.json();report['session_id']=state['session_id']
        for index in range(args.start_points-1,min(args.start_points-1+args.rounds,len(points)-1)):
            response=client.post(f"/api/incremental/sessions/{state['session_id']}/step",json={
                'version':state['version'],'consent':True,'selected_times':[p['t'] for p in points[:args.start_points]] if not state['initialized'] else [points[index]['t']],
                'question':'单轨逐点调试：只根据已开放实测判断接续。一个点不足以确定趋势时保留歧义；两个点给出暂定趋势；后续点核对上次预测并修正。不可查看未来点，不要求照抄参考线。'})
            if response.status_code!=200:
                report['stopped_http_status']=response.status_code;break
            state=response.json();round_=state['rounds'][-1]
            next_point=points[index+1]
            report['rounds'].append({'observation_count':len(state['history']),'visible_until':state['visible_until'],
                'decision':round_['model_answer']['decision'],'model_prediction':state['prediction'],
                'held_out_next_point':next_point,'model_next_error_deg':forecast_error(state['prediction'],next_point),
                'baseline_next_error_deg':forecast_error(round_['input']['baseline_prediction'],next_point)})
            print(json.dumps({k:v for k,v in report['rounds'][-1].items() if k in ('observation_count','visible_until','decision','model_next_error_deg')},ensure_ascii=False),flush=True)
            (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        errors=[r['model_next_error_deg'] for r in report['rounds'] if r['model_next_error_deg'] is not None]
        report['model_next_point_mae_deg']=statistics.mean(errors) if errors else None
        report['evaluated_rounds']=len(errors)
        (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'session_id':report['session_id'],'completed_rounds':len(report['rounds']),
            'evaluated_rounds':len(errors),'mae_deg':report['model_next_point_mae_deg'],
            'stopped_http_status':report.get('stopped_http_status')},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
