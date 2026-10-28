"""The composition root wires the real clients (OpenAI, Chroma, Tavily) without a network
call, so a wiring mistake fails here instead of at container start.
"""

from pathlib import Path

from app.container import build_services
from tests.conftest import make_settings


def test_build_services_wires_the_real_graph_offline(tmp_path: Path) -> None:
    services = build_services(make_settings(tmp_path))

    assert services.graph is not None
    nodes = set(services.graph.get_graph().nodes)
    assert {"router", "knowledge", "support", "escalation", "refusal"} <= nodes
    assert services.knowledge_base_chunks() == 0  # nothing ingested in a fresh store
