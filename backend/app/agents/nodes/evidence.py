"""Agent nodes.evidence definitions."""
import os
from backend.app.agents.prompts import build_association_prompt
from backend.app.services.association.crossing_tracks import crossing_continuity
from backend.app.services.association.identity_association import reconcile_identities
from backend.app.services.association.target_count import audit_count,count_hypothesis,link_tracklets
from backend.app.services.knowledge.rules import retrieve_rules
from backend.app.services.measurements.btr_measurements import measure_pack
from backend.app.services.measurements.sonar_evidence import extract_candidates,resolve_pairs


def retrieve(state):
    rules=retrieve_rules('observation')+retrieve_rules('association')
    rules=list({r['id']:r for r in rules}.values())
    return {'rules':rules,'trace':['retrieve_reviewed_rules']}


def extract(state):
    # Denser measurements, not interpolated points or additional model calls.
    # Both arrays share this grid; old saved R identifiers remain tied to their
    # original evidence pack and must never be reused on this new grid.
    # Dense sampling is experimental: it changed the effective duration of the
    # fixed-point estimation windows and has not passed spatial-accuracy gates.
    try:slices=int(os.environ.get('SONAR_EVIDENCE_TIME_SLICES','16'))
    except ValueError:raise ValueError('SONAR_EVIDENCE_TIME_SLICES必须为16～64的整数') from None
    if not 16<=slices<=64:raise ValueError('SONAR_EVIDENCE_TIME_SLICES必须为16～64的整数')
    evidence={'version':'pixel-candidates-v3','time_slices':slices,'experimental_density':slices!=16,
              'images':[extract_candidates(path,i,state['context'],time_slices=slices)
                                                     for i,path in enumerate(state['images'])],
              'warnings':['绘图区为自动启发式检测，需核对叠加图；候选峰不等于目标。',
                          '已按稳健背景阈值、局部显著性和邻近时间支持过滤噪声；弱目标仍可能漏检，持续杂波仍可能误检。',
                          f'{slices}个同步时间切片，最多每切片6峰；来自实际图像采样，不补点，交叉混合峰和弱信号仍可能漏检。']}
    arrays={(im['platform_id'],im['array']) for im in evidence['images']}
    if any((pid,'hull') not in arrays or (pid,'tow') not in arrays for pid,_ in arrays):
        raise ValueError('每个平台需要同时提供hull和tow图片')
    update={'evidence':evidence,'trace':state['trace']+['extract_pixel_candidates']}
    if state['context'].get('renderer_profile')=='btr_gui_v1':
        report=measure_pack(state['images'],evidence,state['context'])
        update['measurement_report']=report
        evidence['measurement_diagnostics']={'mode':'diagnostic_only','observations_modified':False,
            'images':[{'image_index':im['image_index'],'fitted_groups':im['fitted_groups'],
                       'unavailable_reason':im.get('reason'),
                       'review_groups':[{'candidate_ids':g['candidate_ids'],'status':g['status'],
                                         'normalized_rmse':g.get('normalized_rmse')} for g in im['groups'] if g['status']!='fitted'][:6]}
                      for im in report['images']]}
    return update


def assemble(state):
    prompt=build_association_prompt(state['evidence'],state['rules'],state.get('tracklets'),state.get('count_hypothesis'),state.get('crossing_report'))
    return {'prompt':prompt,'trace':state['trace']+['assemble_observation_association_contract']}


def validate(state):
    return {'observations':resolve_pairs(state['answer'],state['evidence'],isolate_unknown=True,isolate_unsynchronized=True),
            'trace':state.get('trace',[])+['validate_candidate_ids_sync_and_ownership']}


def link(state):
    fragments=link_tracklets(state['evidence'])
    crossing=crossing_continuity(state['evidence'],fragments)
    return {'tracklets':fragments,'crossing_report':crossing,
            'trace':state['trace']+['link_single_array_tracklets_and_x_continuations']}


def count(state):
    return {'count_hypothesis':count_hypothesis(state['tracklets']),'trace':state['trace']+['count_image_hypothesis']}


def audit(state):
    return {'count_report':audit_count(state['answer'],state['evidence'],state.get('tracklets',[]),
             state.get('count_hypothesis',{'min':0,'max':0}),state['observations'],
             isolate_invalid_refs=True,isolate_shared_evidence=True),
            'trace':state['trace']+['audit_count_and_uncovered_tracklets']}


def identity(state):
    observations,report,fragments=reconcile_identities(state['observations'],state['evidence'])
    return {'model_observations':state['observations'],'observations':observations,'identity_report':report,
            'tracklets':fragments,'trace':state['trace']+['reconcile_dual_ridge_identities']}
