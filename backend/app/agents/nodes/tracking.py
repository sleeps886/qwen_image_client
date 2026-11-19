"""Agent nodes.tracking definitions."""
from backend.app.services.knowledge.rules import retrieve_rules
from backend.app.services.localization.continuous_tracks import estimate_continuous,reproject_tracks
from backend.app.services.localization.spatial_tracks import solve_spatial


def locate(state):
    update={'trace':state.get('trace',[])+['spatial_localization']}
    try:
        document=solve_spatial(state['observations'],state['context'])
        update.update(raw_document=document,document=document)
    except ValueError as exc:update['failure']=str(exc)
    return update


def track(state):
    update={'trace':state['trace']+['continuous_trajectory_estimation']}
    if state.get('failure'):
        return update|{'tracking_report':{'status':'skipped','reason':state['failure']}}
    try:document,report=estimate_continuous(state['document'],state['context'])
    except ValueError as exc:return update|{'failure':str(exc),'tracking_report':{'status':'failed','reason':str(exc)}}
    report['knowledge_sources']=retrieve_rules('tracking')
    return update|{'document':document,'tracking_report':report}


def reproject(state):
    update={'trace':state['trace']+['reprojection_quality_check']}
    if state.get('failure'):
        return update|{'reprojection_report':{'status':'skipped','reason':state['failure']}}
    try:document,report=reproject_tracks(state['document'],state['context'],state['evidence'])
    except ValueError as exc:return update|{'failure':str(exc),'reprojection_report':{'status':'failed','reason':str(exc)}}
    report['knowledge_sources']=retrieve_rules('reprojection')
    update.update(document=document,reprojection_report=report)
    if not document['tracks']:update['failure']='连续估计与重投影质检后没有足够的空间轨迹段；不补线或绘制未通过点。'
    return update
