"""Interactive IEEE 9500-node topology debugger.

This first version reads RAW_NODES and RAW_EDGES from the existing HTML viewer,
builds a NetworkX graph, and sends only the selected debug subgraph to Dash.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

import dash
from dash import Input, Output, State, dcc, html
import dash_cytoscape as cyto
import networkx as nx


DEFAULT_HTML = "ieee9500_csip.html"
DEFAULT_GLM = "src/535040422/model_base.glm"

CSIP_LEVELS = [
    ("system", "System", "#ff7043"),
    ("sub_transmission", "Sub-transmission", "#ce93d8"),
    ("substation", "Substation", "#ffd54f"),
    ("feeder", "Feeder", "#64b5f6"),
    ("segment", "Segment", "#ef9a9a"),
    ("service_transformer", "Service transformer", "#80cbc4"),
    ("service_point", "Service point", "#fff176"),
    ("non_topology", "DER / non-topology", "#a5d6a7"),
]
CSIP_COLORS = {level: color for level, _, color in CSIP_LEVELS}
FEEDER_NODE_COLORS = {
    "S1": "#64b5f6",
    "S2": "#81c784",
    "S3": "#ef9a9a",
    "subtrans": "#ffcc80",
}
FEEDER_EDGE_COLORS = {
    "S1": "#1565c0",
    "S2": "#2e7d32",
    "S3": "#b71c1c",
    "subtrans": "#e65100",
}
EDGE_TYPE_COLORS = {
    "triplex_line": "#5a3a7a",
    "service_transformer": "#2e5e3e",
    "transformer": "#8a6020",
    "switch": "#1e6e8a",
    "parent": "#546e7a",
}
DER_COLORS = {
    "solar": "#ffe082",
    "battery": "#80deea",
    "synchronous": "#ef9a9a",
    "diesel": "#ef9a9a",
    "microturbine": "#ef9a9a",
    "steam": "#ef9a9a",
    "natural_gas": "#ef9a9a",
}


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


def parse_glm_objects(glm_path: Path) -> list[dict[str, str]]:
    """Read top-level properties from GLM objects without requiring GridLAB-D."""
    objects: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    depth = 0
    property_pattern = re.compile(r'^\s*(\w+)\s+(?:"([^"]*)"|([^;]+));')

    for line in glm_path.read_text(encoding="utf-8").splitlines():
        if current is None:
            match = re.match(r"^\s*object\s+([\w:.-]+)\s*\{", line)
            if match:
                current = {"object_class": match.group(1)}
                depth = line.count("{") - line.count("}")
            continue

        if depth == 1:
            match = property_pattern.match(line)
            if match:
                current[match.group(1)] = (match.group(2) or match.group(3) or "").strip()

        depth += line.count("{") - line.count("}")
        if depth == 0:
            objects.append(current)
            current = None

    return objects


def diesel_kind(name: str) -> str:
    lowered = name.lower()
    if "microturb" in lowered:
        return "microturbine"
    if "steam" in lowered:
        return "steam"
    if "lng" in lowered:
        return "natural_gas"
    return "diesel"


def add_glm_generators(
    glm_path: Path,
    graph: nx.Graph,
    node_by_id: dict[str, dict[str, Any]],
    edge_by_pair: dict[frozenset[str], list[dict[str, Any]]],
) -> int:
    """Add diesel_dg objects omitted by the original HTML exporter."""
    objects = parse_glm_objects(glm_path)
    parent_by_name = {
        item["name"]: item.get("parent", "")
        for item in objects
        if item.get("name")
    }
    added = 0

    for index, item in enumerate(item for item in objects if item["object_class"] == "diesel_dg"):
        name = item.get("name")
        if not name or name in node_by_id:
            continue

        parent = item.get("parent", "")
        visited: set[str] = set()
        while parent not in node_by_id and parent and parent not in visited:
            visited.add(parent)
            parent = parent_by_name.get(parent, "")
        if not parent:
            continue

        anchor = node_by_id[parent]
        anchor_data = anchor["data"]
        kind = diesel_kind(name)
        data = {
            "id": name,
            "label": name,
            "node_type": "diesel_dg",
            "object_class": "diesel_dg",
            "csip_level": "non_topology",
            "feeder": anchor_data.get("feeder", ""),
            "segment": anchor_data.get("segment", -1),
            "der_name": name.removeprefix("dg_"),
            "der_type": kind,
            "der_ratedS": item.get("Rated_VA", ""),
            "der_phases": item.get("phases", ""),
            "rated_voltage": item.get("Rated_V", ""),
            "parent": item.get("parent", ""),
            "source": "model_base.glm",
        }
        angle = (index % 8) * math.pi / 4
        anchor_position = anchor.get("position", {"x": 0, "y": 0})
        record = {
            "data": data,
            "position": {
                "x": anchor_position["x"] + 34 * math.cos(angle),
                "y": anchor_position["y"] + 34 * math.sin(angle),
            },
        }
        edge = {
            "data": {
                "id": f"parent::{name}",
                "source": parent,
                "target": name,
                "edge_type": "parent",
            }
        }
        node_by_id[name] = record
        graph.add_node(name, **data)
        graph.add_edge(parent, name, **edge["data"])
        edge_by_pair.setdefault(frozenset((parent, name)), []).append(edge)
        added += 1

    return added


def load_topology(html_path: Path, glm_path: Path | None = None):
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
        if attrs.get("csip_level") in {"system", "substation", "sub_transmission"}
    ]

    if not source_nodes:
        # Fallback for datasets that do not explicitly mark the source as system.
        source_nodes = [
            node_id
            for node_id, attrs in graph.nodes(data=True)
            if "source" in node_id.lower() or "swing" in node_id.lower()
        ]

    added_objects = 0
    if glm_path and glm_path.exists():
        added_objects = add_glm_generators(glm_path, graph, node_by_id, edge_by_pair)

    return graph, node_by_id, edge_by_pair, source_nodes, added_objects


def shortest_path_to_source(graph: nx.Graph, node_id: str, sources: list[str]) -> list[str]:
    """Return the shortest connected path from a node to any source candidate."""
    best_path: list[str] = []
    for source in sources:
        if source == node_id:
            continue
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
    searched_edge: str,
    source_path: list[str],
    node_by_id: dict[str, dict[str, Any]],
    edge_by_pair: dict[frozenset[str], list[dict[str, Any]]],
    active_edge_types: set[str] | None = None,
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
        der_color = DER_COLORS.get(str(data.get("der_type", "")))
        feeder_color = FEEDER_NODE_COLORS.get(str(data.get("feeder", "")))
        csip_color = CSIP_COLORS.get(str(data.get("csip_level", "")), "#607d8b")
        if data.get("csip_level") in {"system", "sub_transmission", "substation"}:
            data["visual_color"] = csip_color
        else:
            data["visual_color"] = der_color or feeder_color or csip_color
        level = data.get("csip_level")
        data["visual_size"] = {
            "system": 20,
            "sub_transmission": 12,
            "substation": 14,
            "non_topology": 9,
            "service_transformer": 3.5,
            "service_point": 3,
        }.get(str(level), 6 if data.get("node_type") == "junction" else 3.5)
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
            edge_type = str(original["data"].get("edge_type", ""))
            if active_edge_types is not None and edge_type not in active_edge_types:
                continue
            classes = "trace-edge" if pair in path_pairs else ""
            data = dict(original["data"])
            source = node_by_id.get(str(data.get("source", "")), {}).get("data", {})
            if data.get("edge_type") == "switch" and data.get("normally_open"):
                data["visual_color"] = "#e53935"
            else:
                data["visual_color"] = EDGE_TYPE_COLORS.get(
                    edge_type,
                    FEEDER_EDGE_COLORS.get(str(source.get("feeder", "")), "#37474f"),
                )
            data["visual_width"] = 3 if edge_type == "transformer" else 2 if edge_type == "switch" else 1
            if str(data.get("id")) == searched_edge:
                classes = f"{classes} searched-edge".strip()
            elements.append({"data": data, "classes": classes})

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


def network_stylesheet(theme: str = "dark") -> list[dict[str, Any]]:
    """Build the Cytoscape theme while preserving semantic network colors."""
    is_light = theme == "light"
    primary_label = "#2b2f3a" if is_light else "#fff9c4"
    der_label = "#30343f" if is_light else "#e0e0e0"
    label_border = "#ffffff" if not is_light else "#20242d"
    der_border = "rgba(0,0,0,0.38)" if is_light else "rgba(255,255,255,0.33)"
    return [
        {"selector": "node", "style": {"label": "", "background-color": "data(visual_color)", "width": "data(visual_size)", "height": "data(visual_size)", "border-width": 0, "opacity": 0.9}},
        {"selector": "node[csip_level = 'system'], node[csip_level = 'substation']", "style": {"label": "data(label)", "font-size": 9, "color": primary_label, "text-valign": "bottom", "text-margin-y": 4, "border-width": 2, "border-color": primary_label}},
        {"selector": "node[csip_level = 'non_topology']", "style": {"label": "data(der_name)", "font-size": 7, "color": der_label, "text-valign": "top", "text-margin-y": -2, "shape": "diamond", "border-width": 2, "border-color": der_border}},
        {"selector": "edge", "style": {"line-color": "data(visual_color)", "width": "data(visual_width)", "curve-style": "haystack", "opacity": 0.75}},
        {"selector": ".trace", "style": {"background-color": "#5c6bc0", "width": 7, "height": 7, "border-width": 1, "border-color": "#9fa8da", "opacity": 1, "z-index": 8}},
        {"selector": ".searched", "style": {"background-color": "#ffee58", "border-width": 3, "border-color": label_border, "width": 16, "height": 16, "opacity": 1, "z-index": 10}},
        {"selector": ".source-bus", "style": {"background-color": "#ffa726", "border-width": 2, "border-color": "#ffe0b2", "width": 10, "height": 10, "opacity": 1, "z-index": 9}},
        {"selector": ".trace-edge", "style": {"line-color": "#4fc3f7", "width": 2.5, "opacity": 1, "z-index": 10}},
        {"selector": ".searched-edge", "style": {"line-color": "#ffee58", "width": 3, "opacity": 1, "z-index": 11}},
    ]


def create_app(html_path: Path, glm_path: Path | None = None) -> dash.Dash:
    graph, node_by_id, edge_by_pair, source_nodes, added_objects = load_topology(html_path, glm_path)
    node_ids = sorted(node_by_id)
    edge_by_id = {
        str(record["data"]["id"]): record
        for records in edge_by_pair.values()
        for record in records
    }
    edge_ids = sorted(edge_by_id)
    edge_types = sorted(
        {str(record["data"].get("edge_type", "line")) for record in edge_by_id.values()}
    )
    edge_type_labels = {
        "line": "Distribution lines",
        "triplex_line": "Triplex lines",
        "service_transformer": "Service transformers",
        "transformer": "Substation / regulator transformers",
        "switch": "Switches",
        "parent": "GLM parent connections",
    }
    segments = {
        record["data"].get("segment")
        for record in node_by_id.values()
        if isinstance(record["data"].get("segment"), int)
        and record["data"].get("segment", -1) >= 0
    }
    der_count = sum(bool(record["data"].get("der_name")) for record in node_by_id.values())

    app = dash.Dash(__name__)
    app.title = "IEEE 9500-Node — CSIP Hierarchy Viewer"

    app.layout = html.Div(
        id="page-shell",
        className="page theme-dark",
        children=[
            html.Header(
                className="header",
                children=[
                    html.H1("⚡ IEEE 9500-Node Distribution Network — CSIP Hierarchy Viewer"),
                    html.Div(
                        f"{graph.number_of_nodes():,} nodes · {len(edge_by_id):,} edges · "
                        f"{len(CSIP_LEVELS)} CSIP levels · {len(segments)} segments · {der_count} DERs"
                        + (f" · {added_objects} recovered from GLM" if added_objects else ""),
                        className="header-stats",
                    ),
                    html.Button(
                        "☀ Light mode",
                        id="theme-toggle",
                        className="theme-toggle",
                        title="Switch to light mode",
                        **{"aria-label": "Switch to light mode"},
                    ),
                ],
            ),
            html.Div(
                className="main",
                children=[
            html.Div(
                className="sidebar",
                children=[
                    html.Label("Search node or edge"),
                    dcc.Dropdown(
                        id="object-search",
                        options=(
                            [{"label": f"{node_id}  · node", "value": node_id} for node_id in node_ids]
                            + [{"label": f"{edge_id}  · edge", "value": edge_id} for edge_id in edge_ids]
                        ),
                        placeholder="Object name…",
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
                    html.Label("CSIP layers"),
                    dcc.Checklist(
                        id="csip-layers",
                        className="layer-list",
                        options=[
                            {
                                "label": html.Span(
                                    [html.I(style={"backgroundColor": color}), label]
                                ),
                                "value": level,
                            }
                            for level, label, color in CSIP_LEVELS
                        ],
                        value=[level for level, _, _ in CSIP_LEVELS],
                    ),
                    html.Label("Feeder"),
                    dcc.Checklist(
                        id="feeder-filter",
                        className="layer-list feeder-list",
                        options=[
                            {
                                "label": html.Span([html.I(style={"backgroundColor": color}), label]),
                                "value": feeder,
                            }
                            for feeder, label, color in (
                                ("S1", "S1", FEEDER_NODE_COLORS["S1"]),
                                ("S2", "S2", FEEDER_NODE_COLORS["S2"]),
                                ("S3", "S3", FEEDER_NODE_COLORS["S3"]),
                                ("subtrans", "69 kV", FEEDER_NODE_COLORS["subtrans"]),
                            )
                        ],
                        value=["S1", "S2", "S3", "subtrans"],
                    ),
                    html.Label("Edge types"),
                    dcc.Checklist(
                        id="edge-type-filter",
                        className="layer-list edge-list",
                        options=[
                            {
                                "label": html.Span(
                                    [
                                        html.I(
                                            className="edge-swatch",
                                            style={"backgroundColor": EDGE_TYPE_COLORS.get(edge_type, "#37474f")},
                                        ),
                                        f"{edge_type_labels.get(edge_type, edge_type)} "
                                        f"({sum(record['data'].get('edge_type', 'line') == edge_type for record in edge_by_id.values()):,})",
                                    ]
                                ),
                                "value": edge_type,
                            }
                            for edge_type in edge_types
                        ],
                        value=edge_types,
                    ),
                    html.Button("Show all / reset view", id="reset-view", className="section-btn"),
                    html.Div(id="search-status", className="status"),
                    html.H3("Selected element"),
                    html.Div(id="object-info", className="info-panel"),
                ],
            ),
            html.Div(
                className="graph-wrap",
                children=[
                    cyto.Cytoscape(
                        id="network",
                        layout={"name": "preset", "fit": True, "padding": 40},
                        elements=[],
                        minZoom=0.02,
                        maxZoom=15,
                        style={"width": "100%", "height": "100%"},
                        stylesheet=network_stylesheet("dark"),
                    ),
                    html.Div("🌍 Geographic layout — ~119°W 46.7°N  |  CSIP IEEE 2030.5", className="geo-badge"),
                ],
            ),
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
        Input("csip-layers", "value"),
        Input("feeder-filter", "value"),
        Input("edge-type-filter", "value"),
    )
    def update_subgraph(
        object_id: str | None,
        hops: int,
        options: list[str],
        csip_layers: list[str],
        feeders: list[str],
        selected_edge_types: list[str],
    ):
        active_layers = set(csip_layers or [])
        active_feeders = set(feeders or [])
        active_edges = set(selected_edge_types or [])
        visible = {
            node_id
            for node_id, record in node_by_id.items()
            if record["data"].get("csip_level") in active_layers
            and (
                record["data"].get("feeder") in active_feeders
                or record["data"].get("csip_level") == "system"
            )
        }

        searched_node = object_id if object_id in node_by_id else ""
        searched_edge = object_id if object_id in edge_by_id else ""
        source_path: list[str] = []
        if searched_node:
            selected = nodes_within_hops(graph, searched_node, int(hops or 0))
            if "trace" in (options or []):
                source_path = shortest_path_to_source(graph, searched_node, source_nodes)
            selected = (selected | set(source_path)) & visible | {searched_node}
            detail = node_by_id[searched_node]["data"]
        elif searched_edge:
            edge_data = edge_by_id[searched_edge]["data"]
            endpoints = {str(edge_data["source"]), str(edge_data["target"])}
            selected = set(endpoints)
            for endpoint in endpoints:
                selected |= nodes_within_hops(graph, endpoint, int(hops or 0))
            selected = (selected & visible) | endpoints
            detail = edge_data
        else:
            selected = visible
            detail = None

        elements = make_elements(
            selected,
            searched_node,
            searched_edge,
            source_path,
            node_by_id,
            edge_by_pair,
            active_edges,
        )
        edge_count = sum("target" in element["data"] for element in elements)
        if object_id and not (searched_node or searched_edge):
            status = f"No match for {object_id}."
        elif searched_node and source_path:
            status = f"{len(selected):,} nodes nearby · supply trace to {source_path[-1]}"
        elif searched_node:
            status = f"{len(selected):,} nodes within {hops} hops"
        elif searched_edge:
            status = f"{len(selected):,} nodes around edge {searched_edge}"
        else:
            status = f"Showing {len(selected):,} nodes · {edge_count:,} edges"

        return elements, {"name": "preset", "fit": True, "padding": 40}, status, info_panel(detail)

    @app.callback(
        Output("object-info", "children", allow_duplicate=True),
        Input("network", "tapNodeData"),
        Input("network", "tapEdgeData"),
        prevent_initial_call=True,
    )
    def show_clicked_element(node_data, edge_data):
        triggered_property = dash.ctx.triggered[0]["prop_id"].split(".")[-1]
        return info_panel(node_data if triggered_property == "tapNodeData" else edge_data)

    @app.callback(
        Output("object-search", "value"),
        Output("csip-layers", "value"),
        Output("feeder-filter", "value"),
        Output("edge-type-filter", "value"),
        Input("reset-view", "n_clicks"),
        prevent_initial_call=True,
    )
    def reset_view(_clicks):
        return (
            None,
            [level for level, _, _ in CSIP_LEVELS],
            ["S1", "S2", "S3", "subtrans"],
            edge_types,
        )

    @app.callback(
        Output("page-shell", "className"),
        Output("theme-toggle", "children"),
        Output("theme-toggle", "title"),
        Output("theme-toggle", "aria-label"),
        Output("network", "stylesheet"),
        Input("theme-toggle", "n_clicks"),
    )
    def toggle_theme(n_clicks):
        is_light = bool((n_clicks or 0) % 2)
        if is_light:
            return (
                "page theme-light",
                "☾ Dark mode",
                "Switch to dark mode",
                "Switch to dark mode",
                network_stylesheet("light"),
            )
        return (
            "page theme-dark",
            "☀ Light mode",
            "Switch to light mode",
            "Switch to light mode",
            network_stylesheet("dark"),
        )

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
    parser.add_argument(
        "--glm-file",
        default=DEFAULT_GLM,
        help="Optional GLM used to recover generator objects omitted from the HTML.",
    )
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    source_file = Path(args.html_file).expanduser().resolve()
    if not source_file.exists():
        raise SystemExit(f"HTML file not found: {source_file}")

    glm_file = Path(args.glm_file).expanduser().resolve() if args.glm_file else None
    dash_app = create_app(source_file, glm_file)
    dash_app.run(host=args.host, port=args.port, debug=args.debug)
