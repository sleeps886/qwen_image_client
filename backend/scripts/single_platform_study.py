"""Bounded explicit single-platform experiments; labels are evaluation-only."""
import argparse
import copy
import hashlib
import importlib
import json
from pathlib import Path
import shutil
import sys

from backend.app.agents.forward_inverse_agent import ForwardInverseAgent
from backend.app.agents.model_trajectory import evidence_bundle,validate_final,PROMPT_VERSION
from backend.app.core.config import model_settings
from backend.app.infrastructure.llm.gateway import ConfiguredModelGateway
from backend.app.services.inputs.platform_input import platform_context
from backend.app.services.localization.forward_observation import GeoReference
from backend.app.services.rendering.trajectory import render_document
from backend.scripts.evaluate_inverse import evaluate
from backend.app.repositories.result_repository import ResultRepository,RESULT_FILES


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def read(path):return json.loads(path.read_text(encoding='utf-8'))


def score(document,context,truth,directory):
    converted=copy.deepcopy(document);ref=GeoReference(*context['platforms'][0]['start_ll'])
    if document['coordinate_system']=='local_xy_m':
        for track in converted['tracks']:
            for point in track['points']:
                lat,lon=ref.to_ll((point['x'],point['y']));point.update(x=lon,y=lat)
            track['platform_id']=context['platforms'][0]['name']
            track['mirror_variant']=track.get('branch','main')
        converted['coordinate_system']='lon_lat'
    directory.mkdir();save(directory/'tracks.json',converted)
    report=evaluate(directory,truth)
    save(directory/'evaluation.json',report)
    return {k:report[k] for k in ('reference_target_count','predicted_identity_count','target_count_absolute_error','matched_targets_at_gate','assignments')}


def cloud_round(source,paths,destination,truth=None):
    destination.mkdir(parents=True)
    context=read(source/'calibration_context.json')
    candidates=read(source/('backend_tracks.json' if (source/'backend_tracks.json').exists() else 'tracks.json'))
    for src,name in [('estimated_track.png','backend_candidate.png'),('backend_candidate.png','backend_candidate.png'),('mirror_candidates.png','mirror_candidates.png')]:
        if (source/src).exists():shutil.copyfile(source/src,destination/name)
    fields=read(source/'inverse_fields.json')
    images,prompt,manifest=evidence_bundle(paths,fields,candidates,context,destination,read(source/'inverse_selection.json') if (source/'inverse_selection.json').exists() else None)
    save(destination/'input.json',{'images':manifest,'prompt':prompt,'prompt_version':PROMPT_VERSION,'truth_supplied':False})
    settings=model_settings()
    if not settings.api_key:raise ValueError('测试需要后端已配置模型密钥')
    response=ConfiguredModelGateway(timeout_override=120).invoke(images,prompt,settings,destination/'provider','images')
    answer=response['answer'];(destination/'answer.txt').write_text(answer,encoding='utf-8')
    report={'prompt_version':PROMPT_VERSION,'new_model_calls':1,'usage':response.get('usage'),'truth_supplied':False}
    try:
        document=validate_final(answer,context,candidates)
        from backend.app.services.quality.model_final_quality import audit_geometry,require_geometry
        geometry=audit_geometry(document,candidates,fields,context,read(source/'inverse_selection.json') if (source/'inverse_selection.json').exists() else None)
        save(destination/'geometry_audit.json',geometry);report['geometry_audit']=geometry
        require_geometry(geometry)
        render_document(document,destination,context)
        report.update(status='generated',identity_audit=document['model_identity_audit'])
        if truth:report['evaluation']=score(document,context,truth,destination/'evaluation_only')
    except ValueError as exc:report.update(status='failed',validation_error=str(exc))
    save(destination/'report.json',report)
    print(json.dumps({'round':str(destination),'status':report['status'],'audit':report.get('identity_audit'),'usage':report.get('usage')},ensure_ascii=False),flush=True)
    return report


def publish_round(trial,numeric,output_root):
    """Publish an already executed model round; no requests and no label copying."""
    trial=Path(trial);numeric=Path(numeric)
    report=read(trial/'report.json')
    if report.get('status')!='generated':raise ValueError('不能发布未成功解析的模型实验')
    context=read(numeric/'calibration_context.json')
    candidates=read(numeric/('backend_tracks.json' if (numeric/'backend_tracks.json').exists() else 'tracks.json'))
    answer=(trial/'answer.txt').read_text(encoding='utf-8')
    document=validate_final(answer,context,candidates)
    from backend.app.services.quality.model_final_quality import audit_geometry,require_geometry
    quality=audit_geometry(document,candidates,read(numeric/'inverse_fields.json'),context,read(numeric/'inverse_selection.json'))
    require_geometry(quality)
    report['geometry_audit']=quality
    document['model_geometry_audit']=quality
    if report['geometry_audit']['warning_track_count']:document['warnings'].append('模型坐标回投有疑点；请查看model_final_quality.json。')
    repo=ResultRepository(output_root);run=repo.create_run()
    # Allowlist only inference artifacts. Neither truth nor generation configs enter results.
    for name in RESULT_FILES:
        if name in ('tracks.json','estimated_track.png','answer.txt','response.json','execution_report.json','model_final_report.json','model_final_input.json','model_final_answer.txt','model_final_quality.json'):continue
        p=numeric/name
        if p.is_file() and not p.is_symlink():shutil.copyfile(p,run/name)
    for name in ('backend_candidate.png','mirror_candidates.png','gaussian_0.png','gaussian_1.png'):
        if (trial/name).is_file():shutil.copyfile(trial/name,run/name)
    save(run/'backend_tracks.json',candidates)
    from backend.app.services.rendering.model_views import render_model_views
    render_model_views(document,run,context,candidates)
    for name in ('answer.txt','model_final_answer.txt'):(run/name).write_text(answer,encoding='utf-8')
    save(run/'model_final_input.json',read(trial/'input.json'))
    final={'status':'generated','coordinate_source':'model_output','prompt_version':report['prompt_version'],
           'usage':report['usage'],'identity_audit':document['model_identity_audit'],
           'geometry_audit':report['geometry_audit'],'physical_validation':'observation_consistency_only',
           'automatic_retry':False,'numeric_evidence_reused':True}
    metadata=read(numeric/'execution_report.json')
    metadata.update(result_source='model_visual_fusion',answer_source='model_output',model_final_requested=True,model_final=final,new_model_calls=1,
                    source_result_id=numeric.name,source_node_events=metadata.get('node_events',[]),
                    node_events=[{'event':'node','node':'inverse_model_final','status':'completed',
                                  'summary':'已执行该版本真实模型对照；数值证据复用来源记录，本次未重跑数值反演'}])
    metadata['target_count']={**metadata.get('target_count',{}),'localized_target_count':len({t['id'] for t in document['tracks']})}
    save(run/'model_final_report.json',final);save(run/'execution_report.json',metadata)
    save(run/'model_final_quality.json',report['geometry_audit'])
    save(run/'response.json',{'usage':report['usage'],'new_model_calls':1,'result_source':'model_visual_fusion'})
    return run


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--generator-root',type=Path,required=True)
    parser.add_argument('--source-result',type=Path)
    parser.add_argument('--reuse-study-root',type=Path)
    parser.add_argument('--reuse-case',choices=['generated_1','generated_2'])
    parser.add_argument('--study-dir',type=Path,required=True)
    parser.add_argument('--allow-cloud',action='store_true')
    parser.add_argument('--max-calls',type=int,default=3)
    args=parser.parse_args()
    if not args.allow_cloud or not 1<=args.max_calls<=4:parser.error('真实云端测试必须--allow-cloud并设置1～4次上限')
    args.study_dir.mkdir(parents=True,exist_ok=False);reports=[]
    if args.source_result:
        fields=read(args.source_result/'inverse_fields.json');paths=[];input_dir=None
        for field in fields['images']:
            matches=[p for p in (args.generator_root/'btr_out').rglob('btr_*_'+field['array']+'.png') if hashlib.sha256(p.read_bytes()).hexdigest()==field['sha256']]
            if not matches:raise ValueError('未找到与已存证据SHA256相符的生成器原图')
            paths.append(matches[0]);input_dir=matches[0].parent
        save(args.study_dir/'source_paths.json',{'original_images':[str(p) for p in paths]})
        truth=next(iter((input_dir.parent/'truth').glob('*truth.jsonl')),None)
        if truth is None:truth=next(iter((input_dir.parent/'truth').glob('*.jsonl')),None)
        reports.append(cloud_round(args.source_result,paths,args.study_dir/'source_replay',truth))
        save(args.study_dir/'summary.json',reports)
    if args.reuse_study_root:
        for case in sorted(args.reuse_study_root.glob('generated_*')):
            if args.reuse_case and case.name!=args.reuse_case:continue
            if len(reports)>=args.max_calls:break
            runs=sorted((case/'numeric').glob('*'))
            if not runs:continue
            paths=[case/'input'/f'btr_A_{role}.png' for role in ('hull','tow')]
            report=cloud_round(runs[-1],paths,args.study_dir/case.name,case/'generator_output'/'btr_A_truth.jsonl')
            reports.append(report);save(args.study_dir/'summary.json',reports)
        return
    sys.path.insert(0,str(args.generator_root.resolve()))
    generator=importlib.import_module('btr_gui_server');builder=importlib.import_module('gen_train_ds')
    for index,scenario_index in enumerate((0,2)):
        if len(reports)>=args.max_calls:break
        case=args.study_dir/f'generated_{index+1}';case.mkdir()
        params=builder.build_payload(*builder.SCENARIOS[scenario_index],20261120+index,0)
        assert len(params['platforms'])==1
        params.update(SEED=20261120+index,OUT_DIR=str((case/'generator_output').resolve()),EXPORT_TRUTH=True,SHOW_TRUTH_OVERLAY=False)
        save(case/'generation_params_evaluation_only.json',params)
        generator.run_simulation(params)
        input_dir=case/'input';input_dir.mkdir()
        for role in ('hull','tow'):shutil.copyfile(case/'generator_output'/f'btr_A_{role}.png',input_dir/f'btr_A_{role}.png')
        known={k:params[k] for k in ('T_MAX','DT','B_MIN','B_MAX','REF_LAT','REF_LON','EARTH_R','platforms') if k in params}
        save(input_dir/'platform.json',known)
        context=platform_context(json.dumps(known).encode());context['renderer_profile']='btr_gui_v1'
        paths=[input_dir/f'btr_A_{role}.png' for role in ('hull','tow')]
        local=ForwardInverseAgent();local.run(paths,context,case/'numeric')
        truth=case/'generator_output'/'btr_A_truth.jsonl'
        report=cloud_round(local.directory,paths,case/'model',truth)
        report['numeric_evaluation']=score(read(local.directory/'tracks.json'),context,truth,case/'numeric_evaluation_only')
        reports.append(report);save(args.study_dir/'summary.json',reports)


if __name__=='__main__':main()
