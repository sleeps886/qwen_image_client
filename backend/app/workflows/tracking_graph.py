"""Explicit local nodes 7/8/9: geometry -> continuous estimate -> reprojection."""
from typing import TypedDict
from langgraph.graph import StateGraph,START,END

from backend.app.services.spatial_tracks import solve_spatial
from backend.app.services.continuous_tracks import estimate_continuous,reproject_tracks
from backend.app.services.knowledge import retrieve_rules
from backend.app.workflows.progress import observed


class TrackingState(TypedDict,total=False):
    context:dict
    observations:dict
    evidence:dict
    trace:list[str]
    raw_document:dict
    document:dict
    tracking_report:dict
    reprojection_report:dict
    failure:str


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


def build_tracking_graph(emit=None):
    graph=StateGraph(TrackingState)
    graph.add_node('spatial_localization',observed('spatial_localization',locate,emit))
    graph.add_node('continuous_trajectory_estimation',observed('continuous_trajectory_estimation',track,emit))
    graph.add_node('reprojection_quality_check',observed('reprojection_quality_check',reproject,emit))
    graph.add_edge(START,'spatial_localization')
    graph.add_edge('spatial_localization','continuous_trajectory_estimation')
    graph.add_edge('continuous_trajectory_estimation','reprojection_quality_check')
    graph.add_edge('reprojection_quality_check',END)
    return graph.compile()
