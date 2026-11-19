"""Agent graphs.evidence definitions."""
from langgraph.graph import END,START,StateGraph
from backend.app.agents.nodes.evidence import assemble,audit,count,extract,identity,link,retrieve,validate
from backend.app.agents.progress import observed
from backend.app.agents.state import EvidenceState


def build_preflight(emit=None):
    graph=StateGraph(EvidenceState)
    for name,node,label in [('retrieve',retrieve,'knowledge_retrieval'),('extract',extract,'pixel_candidates'),
                            ('link',link,'tracklet_linking'),('count',count,'count_hypothesis'),('assemble',assemble,'association_context')]:
        graph.add_node(name,observed(label,node,emit))
    graph.add_edge(START,'retrieve');graph.add_edge('retrieve','extract')
    graph.add_edge('extract','link');graph.add_edge('link','count');graph.add_edge('count','assemble');graph.add_edge('assemble',END)
    return graph.compile()


def build_review(emit=None):
    graph=StateGraph(EvidenceState);graph.add_node('validate',observed('pair_validation',validate,emit))
    graph.add_node('audit',observed('count_audit',audit,emit))
    graph.add_node('identity',observed('identity_reconciliation',identity,emit))
    graph.add_edge(START,'validate');graph.add_edge('validate','audit');graph.add_edge('audit','identity');graph.add_edge('identity',END)
    return graph.compile()
