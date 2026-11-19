"""Agent graphs.inverse definitions."""
from langgraph.graph import END,START,StateGraph
from backend.app.agents.nodes.inverse import InverseNodes
from backend.app.agents.progress import INVERSE_NODES
from backend.app.agents.state import InverseState


def build_inverse_graph(agent):
    nodes=InverseNodes(agent)
    functions=[nodes.validate,nodes.field,nodes.paths_node,nodes.associations,nodes.fit,nodes.refine,nodes.review,nodes.quality,nodes.render]
    graph=StateGraph(InverseState)
    for (name,_),function in zip(INVERSE_NODES,functions):
        graph.add_node(name,agent.observed(name,function))
    graph.add_edge(START,INVERSE_NODES[0][0])
    for a,b in zip(INVERSE_NODES,INVERSE_NODES[1:]):graph.add_edge(a[0],b[0])
    graph.add_edge(INVERSE_NODES[-1][0],END)
    return graph
