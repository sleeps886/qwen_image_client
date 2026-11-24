"""Agent graphs.inverse definitions."""
from langgraph.graph import END,START,StateGraph
from backend.app.agents.nodes.inverse import InverseNodes
from backend.app.agents.progress import INVERSE_NODES
from backend.app.agents.state import InverseState


def build_inverse_graph(agent):
    nodes=InverseNodes(agent)
    functions=[nodes.validate,nodes.field,nodes.paths_node,nodes.gaps,nodes.associations,nodes.fit,nodes.refine,nodes.review,nodes.quality,nodes.render]
    graph=StateGraph(InverseState)
    for (name,_),function in zip(INVERSE_NODES,functions):
        graph.add_node(name,agent.observed(name,function))
    graph.add_edge(START,INVERSE_NODES[0][0])
    for a,b in zip(INVERSE_NODES,INVERSE_NODES[1:]):
        if a[0]!='inverse_paths':graph.add_edge(a[0],b[0])
    def gap_route(state):
        if state.get('gap_report',{}).get('candidate_pairs'):return 'review'
        # Real skipped event, not an executed-node claim.
        agent.emit({'event':'node','node':'inverse_gap','label':'断段延续性复核','status':'skipped',
                    'elapsed_ms':0,'summary':'没有满足时间窗的断段组合，跳过复核；未调用模型'})
        return 'continue'
    graph.add_conditional_edges('inverse_paths',gap_route,{'review':'inverse_gap','continue':'inverse_association'})
    if agent.finalizer is not None:
        graph.add_node('inverse_model_final',agent.observed('inverse_model_final',nodes.model_final))
        graph.add_edge(INVERSE_NODES[-1][0],'inverse_model_final')
        graph.add_edge('inverse_model_final',END)
    else:graph.add_edge(INVERSE_NODES[-1][0],END)
    return graph
