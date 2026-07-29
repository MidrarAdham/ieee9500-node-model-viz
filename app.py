"""Interactive IEEE 9500-node topology debugger.

This first version reads RAW_NODES and RAW_EDGES from the existing HTML viewer,
builds a NetworkX graph, and sends only the selected debug subgraph to Dash.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import dash
from dash import Input, Output, State, dcc, html
import dash_cytoscape as cyto
import networkx as nx


DEFAULT_HTML = "ieee9500_csip.html"


def extract_js_array(text: str, variable_name: str) -> list[dict[str, Any]]:
    """Extract a JSON-compatible JavaScript array assigned to a const variable."""
    match = re.search(rf"const\s+{re.escape(variable_name)}\s*=\s*", text)
    if not match:
        raise ValueError(f"Could not find {variable_name} in the HTML file.")

    start = text.find("[", match.end())
    if start < 0:
        raise ValueError(f"Could not find the opening array for {variable_name}.")

    depth = 0
    in_string = False
    escaped = False

    for index in range(start, len(text)):
        char = text[index]

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : index + 1])

    raise ValueError(f"Could not find the closing array for {variable_name}.")


def load_topology(html_path: Path):
    """Load node and edge records and construct a graph."""
    text = html_path.read_text(encoding="utf-8")
    node_records = extract_js_array(text, "RAW_NODES")
    edge_records = extract_js_array(text, "RAW_EDGES")

    graph = nx.Graph()
    node_by_id: dict[str, dict[str, Any]] = {}
    edge_by_pair: dict[frozenset[str], list[dict[str, Any]]] = {}

    for record in node_records:
        data = dict(record["data"])
        node_id = str(data["id"])
        node_by_id[node_id] = record
        graph.add_node(node_id, **data)

    for record in edge_records:
        data = dict(record["data"])
        source = str(data["source"])
        target = str(data["target"])
        if source not in graph or target not in graph:
            continue
        graph.add_edge(source, target, **data)
        edge_by_pair.setdefault(frozenset((source, target)), []).append(record)

    source_nodes = [
        node_id
        for node_id, attrs in graph.nodes(data=True)
        if attrs.get("csip_level") == "system"
    ]

    if not source_nodes:
        # Fallback for datasets that do not explicitly mark the source as system.
        source_nodes = [
            node_id
            for node_id, attrs in graph.nodes(data=True)
            if "source" in node_id.lower() or "swing" in node_id.lower()
        ]

    return graph, node_by_id, edge_by_pair, source_nodes


def shortest_path_to_source(graph: nx.Graph, node_id: str, sources: list[str]) -> list[str]:
    """Return the shortest connected path from a node to any source candidate."""
    best_path: list[str] = []
    for source in sources:
        try:
            path = nx.shortest_path(graph, node_id, source)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            continue
        if not best_path or len(path) < len(best_path):
            best_path = path
    return best_path


def nodes_within_hops(graph: nx.Graph, node_id: str, hops: int) -> set[str]:
    """Return nodes within the requested unweighted graph distance."""
    lengths = nx.single_source_shortest_path_length(graph, node_id, cutoff=hops)
    return set(lengths)


def make_elements(
    selected_nodes: set[str],
    searched_node: str,
    source_path: list[str],
    node_by_id: dict[str, dict[str, Any]],
    edge_by_pair: dict[frozenset[str], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Create Cytoscape elements only for the selected debug subgraph."""
    elements: list[dict[str, Any]] = []
    path_nodes = set(source_path)
    path_pairs = {
        frozenset((source_path[index], source_path[index + 1]))
        for index in range(max(0, len(source_path) - 1))
    }

    for node_id in selected_nodes:
        original = node_by_id[node_id]
        data = dict(original["data"])
        classes = []
        if node_id == searched_node:
            classes.append("searched")
        if node_id in path_nodes:
            classes.append("trace")
        if source_path and node_id == source_path[-1]:
            classes.append("source-bus")

        element = {"data": data, "classes": " ".join(classes)}
        if "position" in original:
            element["position"] = original["position"]
        elements.append(element)

    for pair, records in edge_by_pair.items():
        if not pair.issubset(selected_nodes):
            continue
        for original in records:
            classes = "trace-edge" if pair in path_pairs else ""
            elements.append({"data": dict(original["data"]), "classes": classes})

    return elements


def info_panel(data: dict[str, Any] | None):
    if not data:
        return html.Div("Search for or click an object.", className="muted")

    ignored = {"id", "label"}
    rows = [html.Div([html.Span("ID", className="key"), html.Span(str(data.get("id", "")))])]
    for key, value in data.items():
        if key in ignored or value in ("", None):
            continue
        rows.append(
            html.Div(
                [html.Span(key.replace("_", " ").title(), className="key"), html.Span(str(value))]
            )
        )
    return rows


def create_app(html_path: Path) -> dash.Dash:
    graph, node_by_id, edge_by_pair, source_nodes = load_topology(html_path)
    node_ids = sorted(node_by_id)

    app = dash.Dash(__name__)
    app.title = "IEEE 9500 Debugger"

    app.layout = html.Div(
        className="page",
        children=[
            html.Div(
                className="sidebar",
                children=[
                    html.H2("IEEE 9500 Debugger"),
                    html.Div(f"{graph.number_of_nodes():,} nodes · {graph.number_of_edges():,} connections", className="muted"),
                    html.Label("Object name"),
                    dcc.Dropdown(
                        id="object-search",
                        options=[{"label": node_id, "value": node_id} for node_id in node_ids],
                        placeholder="Type an exact object name…",
                        searchable=True,
                        clearable=True,
                    ),
                    html.Label("Neighbor hops"),
                    dcc.Slider(id="hop-count", min=0, max=10, step=1, value=2, marks={i: str(i) for i in range(0, 11, 2)}),
                    dcc.Checklist(
                        id="trace-options",
                        options=[{"label": " Trace to source bus", "value": "trace"}],
                        value=["trace"],
                    ),
                    html.Div(id="search-status", className="status"),
                    html.H3("Selected object"),
                    html.Div(id="object-info", className="info-panel"),
                ],
            ),
            html.Div(
                className="graph-wrap",
                children=[
                    cyto.Cytoscape(
                        id="network",
                        layout={"name": "preset", "fit": True, "padding": 35},
                        elements=[],
                        minZoom=0.05,
                        maxZoom=8,
                        style={"width": "100%", "height": "100%"},
                        stylesheet=[
                            {"selector": "node", "style": {"label": "data(label)", "font-size": 8, "background-color": "#607d8b", "width": 12, "height": 12, "color": "#dbe4ff", "text-outline-width": 2, "text-outline-color": "#111827"}},
                            {"selector": "edge", "style": {"line-color": "#455a64", "width": 1, "curve-style": "bezier", "opacity": 0.7}},
                            {"selector": ".trace", "style": {"background-color": "#42a5f5", "width": 17, "height": 17, "z-index": 10}},
                            {"selector": ".searched", "style": {"background-color": "#ffca28", "border-width": 3, "border-color": "#fff3c4", "width": 24, "height": 24, "z-index": 20}},
                            {"selector": ".source-bus", "style": {"background-color": "#66bb6a", "shape": "diamond", "width": 27, "height": 27, "z-index": 20}},
                            {"selector": ".trace-edge", "style": {"line-color": "#42a5f5", "width": 4, "opacity": 1, "z-index": 10}},
                        ],
                    )
                ],
            ),
        ],
    )

    @app.callback(
        Output("network", "elements"),
        Output("network", "layout"),
        Output("search-status", "children"),
        Output("object-info", "children"),
        Input("object-search", "value"),
        Input("hop-count", "value"),
        Input("trace-options", "value"),
    )
    def update_subgraph(node_id: str | None, hops: int, options: list[str]):
        if not node_id:
            return [], {"name": "preset"}, "Select an object to begin.", info_panel(None)

        nearby = nodes_within_hops(graph, node_id, int(hops or 0))
        source_path = shortest_path_to_source(graph, node_id, source_nodes) if "trace" in (options or []) else []
        selected = nearby | set(source_path)
        elements = make_elements(selected, node_id, source_path, node_by_id, edge_by_pair)

        if source_path:
            status = f"Showing {len(selected):,} nodes. Source trace: {len(source_path) - 1:,} edges to {source_path[-1]}."
        elif "trace" in (options or []):
            status = f"Showing {len(selected):,} nodes. No path to a recognized source bus was found."
        else:
            status = f"Showing {len(selected):,} nodes within {hops} hops."

        return elements, {"name": "preset", "fit": True, "padding": 40}, status, info_panel(node_by_id[node_id]["data"])

    @app.callback(
        Output("object-info", "children", allow_duplicate=True),
        Input("network", "tapNodeData"),
        prevent_initial_call=True,
    )
    def show_clicked_node(data):
        return info_panel(data)

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the IEEE 9500 interactive debugger.")
    parser.add_argument(
        "html_file",
        nargs="?",
        default=DEFAULT_HTML,
        help="Path to the existing IEEE 9500 HTML viewer.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8050)
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    source_file = Path(args.html_file).expanduser().resolve()
    if not source_file.exists():
        raise SystemExit(f"HTML file not found: {source_file}")

    dash_app = create_app(source_file)
    dash_app.run(host=args.host, port=args.port, debug=args.debug)
