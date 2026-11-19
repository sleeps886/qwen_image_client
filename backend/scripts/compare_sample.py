"""Repeatable development loop: input-only inference, THEN offline comparison.

Never imported by HTTP or agent code. Reference-selected branches are only drawn
in the explicitly marked offline panel, never written into production tracks.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import time
import numpy as np
from PIL import Image,ImageDraw
from scipy.ndimage import distance_transform_edt

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from backend.app.services.inputs.input_service import prepare_input
from backend.app.services.evaluation.comparison import compare_images
from backend.app.agents.forward_inverse_agent import ForwardInverseAgent
from backend.scripts.evaluate_inverse import evaluate


def aligned_outline(labels,tracks,directory,name,tolerance_m=150.):
    """Shared metric coordinates: no independent cropping, translation or scaling."""
    latitude=float(np.mean([p['lat'] for rows in labels.values() for p in rows]))
    def xy(lon,lat):return (6371000*np.cos(np.radians(latitude))*np.radians(lon),6371000*np.radians(lat))
    lines=[[xy(p['lon'],p['lat']) for p in rows] for rows in labels.values()]
    predictions=[[xy(p['x'],p['y']) for p in t['points']] for t in tracks]
    points=np.array([p for line in lines+predictions for p in line]);lo=points.min(0)-300;hi=points.max(0)+300
    size=(1280,720);step=(hi-lo)/np.array([size[0]-1,size[1]-1])
    def raster(polylines):
        canvas=Image.new('1',size);draw=ImageDraw.Draw(canvas)
        for line in polylines:
            pixels=[tuple(map(float,(np.array(p)-lo)/step)) for p in line]
            if len(pixels)>1:draw.line(pixels,fill=1,width=1)
        return np.array(canvas,dtype=bool)
    reference,prediction=raster(lines),raster(predictions)
    reference_distance=distance_transform_edt(~reference,sampling=step[::-1])
    prediction_distance=distance_transform_edt(~prediction,sampling=step[::-1])
    precision=float((reference_distance[prediction]<=tolerance_m).mean()) if prediction.any() else 0.
    recall=float((prediction_distance[reference]<=tolerance_m).mean()) if reference.any() else 0.
    f1=2*precision*recall/max(1e-12,precision+recall)
    overlay=np.full((*reference.shape,3),[9,15,25],dtype=np.uint8)
    overlay[reference]=[0,220,235];overlay[prediction]=[255,80,180];overlay[reference&prediction]=[255,255,255]
    Image.fromarray(overlay[::-1]).save(Path(directory)/name)
    return {'metric':'shared_metric_frame_target_outline_f1','f1_percent':round(f1*100,2),
        'precision_percent':round(precision*100,2),'visible_reference_coverage_percent':round(recall*100,2),
        'tolerance_m':tolerance_m,'independent_recenter_or_scale':False,'uses_reference_to_select_side':False,
        'notes':'同一米制框架，仅目标线条；150米为开发诊断容差，不是定位精度或统计置信区间。'}


def compare_result(directory,sample,gate_m=250.):
    directory=Path(directory);sample=Path(sample)
    document=json.loads((directory/'tracks.json').read_text(encoding='utf-8'))
    before=(directory/'tracks.json').read_bytes()
    report=evaluate(directory,sample/'truth/btr_A_truth.jsonl',gate_m)
    labels=defaultdict(list)
    for line in (sample/'truth/btr_A_truth.jsonl').read_text(encoding='utf-8').splitlines():
        if line.strip():
            p=json.loads(line);labels[p['target_id']].append(p)
    matches={a['predicted_identity']:a for a in report['assignments']}
    colors=['#ffa600','#ed00d5','#00d9eb','#9bff00','#859cff','#ff6d7c']
    color={target:colors[i%len(colors)] for i,target in enumerate(sorted(labels))}
    plt.style.use('dark_background');fig,axes=plt.subplots(1,3,figsize=(18,6),sharex=True,sharey=True)
    for ax in axes:
        ax.set_facecolor('#0a0f1f');ax.set_xlabel('Longitude (deg)');ax.grid(alpha=.15)
        for target,rows in labels.items():
            rows.sort(key=lambda p:p['time_s'])
            ax.plot([p['lon'] for p in rows],[p['lat'] for p in rows],color=color[target],lw=2,alpha=.7,label=target+' reference')
    axes[0].set_title('Reference: visible-time truth only')
    axes[1].set_title('Runtime output: ALL alternatives (dashed)')
    axes[2].set_title('OFFLINE oracle-side diagnostic only\nNOT the uniquely inferred runtime result')
    for track in document['tracks']:
        match=matches.get(track['id']);c=color[match['reference_target']] if match else '#ffffff'
        points=track['points']
        axes[1].plot([p['x'] for p in points],[p['y'] for p in points],color=c,ls='--',lw=1)
        alternative=str(track.get('mirror_variant',track.get('handedness','unresolved')))
        if match and alternative==match['oracle_best_alternative']:
            axes[2].plot([p['x'] for p in points],[p['y'] for p in points],color=c,ls='--',lw=1.5)
    axes[0].set_ylabel('Latitude (deg)');axes[0].legend(fontsize=8)
    fig.tight_layout();fig.savefig(directory/'offline_coordinate_comparison.png',dpi=120);plt.close(fig)
    reference_png=(sample/'truth/btr_A_track.png').read_bytes()
    if (directory/'mirror_candidates.png').is_file():
        report['primary_display_outline']=compare_images(reference_png,(directory/'estimated_track.png').read_bytes(),directory)
    all_image=directory/('mirror_candidates.png' if (directory/'mirror_candidates.png').is_file() else 'estimated_track.png')
    report['visual_outline']=compare_images(reference_png,all_image.read_bytes(),directory)
    report['sample_directory']=str(sample.resolve())
    report['primary_geometry_outline']=aligned_outline(labels,[t for t in document['tracks'] if t.get('mirror_variant',0)==0],directory,'offline_primary_aligned_overlay.png')
    report['all_mirrors_geometry_outline']=aligned_outline(labels,document['tracks'],directory,'offline_all_mirrors_aligned_overlay.png')
    report['input_sha256']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((sample/'input').iterdir()) if p.is_file()}
    report['development_sample_not_independent_test']=True
    sources=['backend/app/services/measurements/btr_field.py','backend/app/services/localization/batch_inverse.py','backend/app/services/localization/profile_inverse.py',
             'backend/app/services/localization/motion_families.py','backend/app/services/localization/observability.py']
    report['comparison_time_code_sha256_not_historical_inference']={p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in sources}
    report['runtime_tracks_unchanged_by_evaluation']=(directory/'tracks.json').read_bytes()==before
    (directory/'offline_evaluation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sample_directory');parser.add_argument('--result-root',default='work/sample005-iterations')
    parser.add_argument('--existing-result');parser.add_argument('--gate-m',type=float,default=250.)
    args=parser.parse_args();sample=Path(args.sample_directory);started=time.monotonic()
    if args.existing_result:directory=Path(args.existing_result)
    else:
        source=sample/'input'
        context=prepare_input(2,(source/'platform.json').read_bytes(),'','images',renderer_profile='btr_gui_v1').context
        workflow=ForwardInverseAgent()
        # No sample/truth directory is handed to the production workflow.
        workflow.run([source/'btr_A_hull.png',source/'btr_A_tow.png'],context,Path(args.result_root))
        directory=workflow.directory
    report=compare_result(directory,sample,args.gate_m)
    print(json.dumps({'directory':str(directory),'elapsed_s':round(time.monotonic()-started,2),
        'count':[report['reference_target_count'],report['predicted_identity_count']],
        'outline_percent':report['visual_outline']['similarity_percent'],
        'oracle_side_rmse_m':{a['reference_target']:round(a['oracle_best_alternative_rmse_m'],2) for a in report['assignments']},
        'primary_side_rmse_m':{a['reference_target']:round(a['primary_alternative_rmse_m'],2) for a in report['assignments']}},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
