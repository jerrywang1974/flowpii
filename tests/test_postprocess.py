from flowpii.models import Action, BifGraph, Carry, Edge, MediaType, Metadata, Node
from flowpii.postprocess import normalize_graph
from flowpii.expand import expand_graph


def test_split_dengda_lieyin_directions():
    graph = BifGraph(
        metadata=Metadata(process_id="1-1-8", process_name="支單作業"),
        nodes=[
            Node(id="sec", name="資安暨個資管理室", lane="department"),
            Node(id="erp", name="ERP系統", lane="system", note="支出作業，fnot", note_style="inline"),
        ],
        assets=[],
        edges=[
            Edge(
                **{
                    "from": "sec",
                    "to": "erp",
                    "actions": [
                        Action(method="登打", protection="權限"),
                        Action(method="列印", protection="權限"),
                    ],
                    "carries": [Carry(code="007", media=[MediaType.digital, MediaType.paper])],
                    "bidirectional": False,
                }
            )
        ],
    )
    g2 = normalize_graph(graph)
    assert len(g2.edges) == 2
    rows = expand_graph(g2)
    assert len(rows) == 2
    methods = {(r.source, r.target, r.transfer_method, r.file_type) for r in rows}
    assert ("資安暨個資管理室", "ERP系統[支出作業，fnot]", "登打", "2. 電子檔") in methods
    assert ("ERP系統[支出作業，fnot]", "資安暨個資管理室", "列印", "1. 紙本") in methods


def test_sharepoint_rename():
    graph = BifGraph(
        nodes=[
            Node(id="sp", name="Sharepoint", lane="system", note="資安暨個資管理室", note_style="inline")
        ]
    )
    g2 = normalize_graph(graph)
    assert g2.nodes[0].name == "資安室sharepoint"
