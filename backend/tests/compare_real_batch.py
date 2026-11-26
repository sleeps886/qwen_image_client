"""Post-inference evaluation ONLY. Never feeds reference/source labels to model."""
import collections
import json
import math
import sys
import zipfile
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def main(root):
    run=json.loads((root/'run-resumed-54.json').read_text(encoding='utf-8'));v=run['working_view']
    origin=v['spatial_preview']['origin_lat_lon'];truth={};truth_paths={}
    for i in (1,2,3):
        with zipfile.ZipFile(f'C:/Users/LENOVO/AppData/Local/Temp/btr_target_00{i} (2).zip') as z:
            reference=json.loads(z.read('evaluation/reference.json'))
        points={p['t']:(6371000*math.cos(math.radians(origin[0]))*math.radians(p['longitude']-origin[1]),6371000*math.radians(p['latitude']-origin[0])) for p in reference['points']}
        truth_paths[f'I0{i}']=points
    table={};pairs=[];lookup={p['candidate_id']:p for p in v['points']}
    for u in v['units']:
        cid=u['candidate_ids'][0];source=cid.split('-')[0];target=v['membership'][cid]['target_id']
        truth[u['unit_id']]=truth_paths[source][lookup[cid]['t']]
        table.setdefault(target,collections.Counter())[source]+=1;pairs.append((source,target))
    total_pairs=len(pairs)*(len(pairs)-1)//2
    errors=sum((a[0]==b[0])!=(a[1]==b[1]) for i,a in enumerate(pairs) for b in pairs[i+1:])
    report={'evaluation_only':True,'reference_used_as_model_input':False,'reference_targets':3,
        'model_target_hypotheses':len(table),'source_counts_per_model_target':table,
        'pairwise_partition_agreement':round(1-errors/total_pairs,4),'appearance_matches_three_complete_lines':False,
        'note':'No cross-source mixing in this run, but 2 true paths split into 2 hypotheses each. Mirror choice is display-only.'}
    fig,axes=plt.subplots(1,3,figsize=(17,5.5),facecolor='#0d1424')
    colors=['#45c9ad','#efb85f','#8fb4ff','#c692ed','#f17d7d']
    for ax in axes:
        ax.set_facecolor('#0d1424');ax.tick_params(colors='#bed0df');ax.set_aspect('equal',adjustable='datalim');ax.grid(alpha=.16,color='#b8cbdc');ax.set_xlabel('East (m)',color='#bed0df');ax.set_ylabel('North (m)',color='#bed0df')
    for color,(source,path) in zip(colors,truth_paths.items()):
        ps=list(path.values());axes[0].plot([p[0] for p in ps],[p[1] for p in ps],color=color,label=source)
    axes[0].set_title('Reference: 3 trajectories (evaluation only)',color='white');axes[0].legend()
    for ax,side in zip(axes[1:],('left','right')):
        d=json.loads((root/f'display-{side}.json').read_text(encoding='utf-8'));distances=[]
        for color,tr in zip(colors,d['tracks']):
            ps=tr['points'];ax.scatter([p['x'] for p in ps],[p['y'] for p in ps],s=12,color=color)
            for a,b in zip(ps,ps[1:]):
                if b['connect_previous']:ax.plot([a['x'],b['x']],[a['y'],b['y']],color=color,lw=1.1)
        for g in d['groups']:
            if g['selected']:
                p=g['selected']['point'];q=truth[g['unit_id']];distances.append(math.hypot(p['x']-q[0],p['y']-q[1]))
        report[f'{side}_display_rmse_m']=round(math.sqrt(sum(x*x for x in distances)/len(distances)),2)
        ax.set_title(f'Model assignments: {len(d["tracks"])} lines, {side} convention',color='white')
    fig.tight_layout();fig.savefig(root/'trajectory-comparison.png',dpi=150,facecolor=fig.get_facecolor());plt.close(fig)
    (root/'trajectory-comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main(Path(sys.argv[1]))
