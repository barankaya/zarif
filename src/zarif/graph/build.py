"""Assembles the LangGraph StateGraph described in spec.md section 4."""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from .nodes import (
    human_review_node,
    make_extract_critical_node,
    make_propose_edit_node,
    make_verify_retention_node,
    parse_node,
    route_after_verification,
    write_node,
)
from .state import ZarifState


def build_graph(editor_model, verifier_model, checkpointer=None):
    graph = StateGraph(ZarifState)

    graph.add_node("parse", parse_node)
    graph.add_node("extract_critical", make_extract_critical_node(editor_model))
    graph.add_node("propose_edit", make_propose_edit_node(editor_model))
    graph.add_node("verify_retention", make_verify_retention_node(verifier_model))
    graph.add_node("human_review", human_review_node)
    graph.add_node("write", write_node)

    graph.set_entry_point("parse")
    graph.add_edge("parse", "extract_critical")
    graph.add_edge("extract_critical", "propose_edit")
    graph.add_edge("propose_edit", "verify_retention")
    graph.add_conditional_edges(
        "verify_retention",
        route_after_verification,
        {"propose_edit": "propose_edit", "human_review": "human_review"},
    )
    graph.add_edge("human_review", "write")
    graph.add_edge("write", END)

    # interrupt_before=["write"] gives the CLI a pause point to ask for human approval
    # before anything touches disk (Prensip 3).
    return graph.compile(checkpointer=checkpointer or MemorySaver(), interrupt_before=["write"])
