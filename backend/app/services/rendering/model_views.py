"""Separate representative model paths from all alternatives; never use labels."""
import copy
import re
from backend.app.services.rendering.trajectory import render_document


def render_model_views(document,directory,context,candidates=None):
    aliases={t['id']+'|'+t.get('branch','main'):t.get('mirror_variant') for t in (candidates or {}).get('tracks',[])}
    def side(track):
        known={aliases[s] for s in track.get('source_candidate_ids',[]) if s in aliases and aliases[s] is not None}
        if len(known)==1:return ('source_variant',next(iter(known)))
        match=re.search(r'(?:^|:)alternative(\d+)(?::|$)',track.get('branch',''))
        if match:return ('source_variant',int(match[1]))
        return ('model_branch',track.get('mirror_variant',track.get('branch','main')))
    first={}
    for track in document['tracks']:first.setdefault(track['id'],side(track))
    primary=copy.deepcopy(document)
    primary['tracks']=[t for t in primary['tracks'] if side(t)==first[t['id']]]
    primary['display_view']='model_primary_hypotheses'
    document['display_view']='model_primary_hypotheses'
    document['primary_display']={'selection_policy':'first_model_branch_per_identity_no_truth',
                                 'identity_count':len(first),'displayed_segments':len(primary['tracks']),
                                 'all_candidate_segments':len(document['tracks']),'side_verified':False}
    warning='主图按模型输出顺序展示每个身份的一组代表分支，未确认唯一侧别；全部模型镜像候选保留在model_mirror_candidates.png和tracks.json。颜色不代表与参考图目标一一对应。'
    if warning not in document['warnings']:document['warnings'].append(warning)
    full=copy.deepcopy(document);full['display_view']='model_visual_fusion'
    render_document(full,directory,context,filename='model_mirror_candidates.png',persist=False)
    render_document(primary,directory,context,persist=False)
    for key in ('coordinate_reference',):
        if key in primary:document[key]=primary[key]
    from backend.app.repositories.result_repository import ResultRepository
    ResultRepository.save(directory,'tracks.json',document)
    return document
