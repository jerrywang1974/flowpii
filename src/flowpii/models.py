from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Lane(str, Enum):
    third_party = "third_party"
    department = "department"
    system = "system"
    data = "data"


class MediaType(str, Enum):
    digital = "D"
    paper = "P"


class Action(BaseModel):
    method: str = Field(description="傳輸方式，如 Email、上傳、親送")
    protection: str | None = Field(
        default=None, description="保護措施原文，如 X、權限、專人"
    )


class Carry(BaseModel):
    code: str = Field(description="資料檔編號，如 002")
    media: list[MediaType] = Field(description="此邊承載的型態 D/P")


class Node(BaseModel):
    id: str
    name: str
    lane: Lane | str = Lane.department
    note: str | None = None
    # How to render note in Excel AF/AG to match golden samples
    note_style: Literal["newline", "inline", "none"] = "newline"

    def display_name(self) -> str:
        if self.note and self.note_style == "newline":
            return f"{self.name}\n[{self.note}]"
        if self.note and self.note_style == "inline":
            return f"{self.name}[{self.note}]"
        return self.name


class Asset(BaseModel):
    code: str
    name: str
    media: list[MediaType] = Field(default_factory=lambda: [MediaType.digital])

    def inventory_name(self) -> str:
        return f"{self.code}_{self.name}"


class Edge(BaseModel):
    from_id: str = Field(alias="from")
    to_id: str = Field(alias="to")
    actions: list[Action] = Field(default_factory=list)
    carries: list[Carry] = Field(default_factory=list)
    bidirectional: bool = False

    model_config = {"populate_by_name": True}


class Metadata(BaseModel):
    process_id: str = ""
    process_name: str = ""
    department: str = ""
    controller_or_processor: str | None = None
    bif_version: str = ""
    inventory_date: str = ""
    inventory_version: str = ""
    company: str | None = None


class BifGraph(BaseModel):
    metadata: Metadata = Field(default_factory=Metadata)
    nodes: list[Node] = Field(default_factory=list)
    assets: list[Asset] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)

    def node_map(self) -> dict[str, Node]:
        return {n.id: n for n in self.nodes}

    def asset_map(self) -> dict[str, Asset]:
        return {a.code: a for a in self.assets}


class InventoryRow(BaseModel):
    process_id: str
    process_name: str
    file_name: str
    file_type: Literal["1. 紙本", "2. 電子檔"]
    source: str
    target: str
    transfer_method: str

    def as_dict(self) -> dict[str, str]:
        return {
            "A": self.process_id,
            "B": self.process_name,
            "G": self.file_name,
            "H": self.file_type,
            "AF": self.source,
            "AG": self.target,
            "AH": self.transfer_method,
        }


class RecognizeResult(BaseModel):
    graph: BifGraph
    rows: list[InventoryRow] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    confirmed: bool = False
