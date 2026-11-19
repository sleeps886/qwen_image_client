"""Agent graphs.tracking definitions."""
from langgraph.graph import END,START,StateGraph
from backend.app.agents.nodes.tracking import locate,reproject,track
from backend.app.agents.progress import observed
from backend.app.agents.state import TrackingState


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
