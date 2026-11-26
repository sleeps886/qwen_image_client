"""Offline all-point comparison artifact; never calls a model or adopts a proposal."""
import argparse
import copy
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from backend.app.api.routers.incremental import get_gaussian_batch
from backend.app.services.association.incremental_prediction import create_state
from backend.app.services.association.prediction_review import all_point_proposal


def main():
    parser=argparse.ArgumentParser(description='离线试绘保存批次的所有候选目标，保留镜像与未关联点')
    parser.add_argument('result_id');parser.add_argument('--output',default='work/all-point-real')
    args=parser.parse_args();output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
    batch=json.loads(get_gaussian_batch(args.result_id).body)
    fig,axes=plt.subplots(1,2,figsize=(14,6));report=[];used=set()
    for array,marker in [('hull','.'),('tow','x')]:
        points=[p for p in batch['candidates'] if p['array']==array]
        axes[0].scatter([p['t'] for p in points],[p['bearing_deg'] for p in points],s=8,c='#aaa',alpha=.4,marker=marker,label='All '+array+' candidates')
    colors=['#df9164','#62b762','#6e9ae0','#bb83cb','#d3ac42']
    for index,target in enumerate(batch['target_catalog']):
        ids=set(target['candidate_ids']);used.update(ids)
        data=copy.deepcopy(batch);data['candidates']=[p for p in data['candidates'] if p['candidate_id'] in ids]
        seed=next((p for p in data['candidates'] if p['array']=='hull'),data['candidates'][0])
        proposal=all_point_proposal(create_state(data,[seed['candidate_id']]))
        current=proposal['rounds'][0];plan=current['spatial_plan'];color=colors[index%len(colors)]
        history=proposal['history'];axes[0].plot([p['t'] for p in history],[p['bearing_deg'] for p in history],'o',ms=3,label=target['target_id'],color=color)
        for branch in plan['branches']:
            for segment in branch['observed_segments']:
                axes[1].plot([p['x'] for p in segment],[p['y'] for p in segment],'--o',ms=2,color=color)
        report.append({'target_id':target['target_id'],'input_points':len(data['candidates']),
            'times':len(current['input']['selected_time_groups']),'paired_times':plan['paired_observation_count'],
            'branches':len(plan['branches']),'status':plan['status'],
            'excluded_ambiguous_times':current['excluded_ambiguous_times'],'missing_primary_times':current['missing_primary_times']})
    axes[0].set(xlabel='Time (s)',ylabel='Folded bearing (deg)',title='All candidates / target hypotheses / no extrapolation')
    axes[0].legend(fontsize=8);axes[0].grid(alpha=.2)
    axes[1].scatter([0],[0],marker='^',color='black',label='Stationary platform A')
    axes[1].set(xlabel='East (m)',ylabel='North (m)',title='Unadopted dual-array candidates / mirrors retained')
    axes[1].set_aspect('equal',adjustable='datalim');axes[1].grid(alpha=.2);axes[1].legend()
    fig.tight_layout();fig.savefig(str(output)+'-preview.png',dpi=140);plt.close(fig)
    summary={'source_result_id':args.result_id,'real_model_calls':0,'adopted':False,
        'batch_points':len(batch['candidates']),'associated_unique_points':len(used),
        'unassociated_points':len(batch['candidates'])-len(used),'targets':report,
        'comparison':'Different algorithms: instantaneous dual-array intersections vs workbench whole-track motion fitting. Not equivalent.'}
    Path(str(output)+'-report.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':main()
