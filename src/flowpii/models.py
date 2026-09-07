from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, Field


def _none_to_empty(v: Any) -> Any:
    return "" if v is None else v


def _none_to_none_or_str(v: Any) -> Any:
    if v is None:
        return None
    return str(v)


def _coerce_str(v: Any) -> Any:
    if v is None:
        return ""
    return str(v)


def _coerce_code(v: Any) -> Any:
    if v is None:
        return ""
    # LLM sometimes returns 1 / 2 / 10 as int
    if isinstance(v, int):
        return f"{v:03d}" if v < 1000 else str(v)
    return str(v).strip()


def _coerce_note_style(v: Any) -> Any:
    if v is None or v == "":
        return "newline"
    if v not in {"newline", "inline", "none"}:
        return "newline"
    return v


EmptyStr = Annotated[str, BeforeValidator(_none_to_empty)]
OptStr = Annotated[str | None, BeforeValidator(_none_to_none_or_str)]
CodeStr = Annotated[str, BeforeValidator(_coerce_code)]
ReqStr = Annotated[str, BeforeValidator(_coerce_str)]


class Lane(str, Enum):
    third_party = "third_party"
    department = "department"
    system = "system"
    data = "data"


class MediaType(str, Enum):
    digital = "D"
    paper = "P"


def _coerce_media_list(v: Any) -> Any:
    if v is None or v == []:
        return [MediaType.digital]
    if isinstance(v, str):
        return [v]
    return v


class Action(BaseModel):
    method: ReqStr = Field(description="傳輸方式，如 Email、上傳、親送")
    protection: OptStr = Field(
        default=None, description="保護措施原文，如 X、權限、專人"
    )


class Carry(BaseModel):
    code: CodeStr = Field(description="資料檔編號，如 002")
    media: Annotated[list[MediaType], BeforeValidator(_coerce_media_list)] = Field(
        default_factory=lambda: [MediaType.digital],
        description="此邊承載的型態 D/P",
    )


class Node(BaseModel):
    id: ReqStr
    name: ReqStr
    lane: Lane | str = Lane.department
    note: OptStr = None
    # How to render note in Excel AF/AG to match golden samples
    note_style: Annotated[
        Literal["newline", "inline", "none"], BeforeValidator(_coerce_note_style)
    ] = "newline"

    def display_name(self) -> str:
        if self.note and self.note_style == "newline":
            return f"{self.name}\n[{self.note}]"
        if self.note and self.note_style == "inline":
            return f"{self.name}[{self.note}]"
        return self.name


class Asset(BaseModel):
    code: CodeStr
    name: ReqStr
    media: Annotated[list[MediaType], BeforeValidator(_coerce_media_list)] = Field(
        default_factory=lambda: [MediaType.digital]
    )

    def inventory_name(self) -> str:
        return f"{self.code}_{self.name}"


class Edge(BaseModel):
    from_id: ReqStr = Field(alias="from")
    to_id: ReqStr = Field(alias="to")
    actions: list[Action] = Field(default_factory=list)
    carries: list[Carry] = Field(default_factory=list)
    bidirectional: bool = False

    model_config = {"populate_by_name": True}


class Metadata(BaseModel):
    process_id: EmptyStr = ""
    process_name: EmptyStr = ""
    department: EmptyStr = ""
    controller_or_processor: OptStr = None
    bif_version: EmptyStr = ""
    inventory_date: EmptyStr = ""
    inventory_version: EmptyStr = ""
    company: OptStr = None


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
