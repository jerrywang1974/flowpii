"""BIF diagram symbol dictionary for ISO 27701 PII inventory charts."""

from __future__ import annotations

# Methods that typically imply paper (P) when C carries both D and P
PAPER_METHODS = {
    "親送",
    "親取",
    "內郵",
    "fax",
    "Fax",
    "FAX",
    "列印",
    "臨櫃",
    "郵寄",
    "快遞",
}

# Methods that typically imply digital (D)
DIGITAL_METHODS = {
    "Email",
    "E-mail",
    "email",
    "e-mail",
    "Line",
    "LINE",
    "上傳",
    "下載",
    "登打",
    "系統拋轉",
    "查詢",
    "掃描",
    "推播",
    "儲存",
    "讀取",
    "FTP",
    "電話",
    "傳真",  # sometimes listed; Fax above preferred for paper
    "隨身碟",
    "光碟",
}

PROTECTION_MAP = {
    "X": "╳",
    "x": "╳",
    "╳": "╳",
    "權限": "權限控管",
    "專人": "專人",
}

FILE_TYPE_PAPER = "1. 紙本"
FILE_TYPE_DIGITAL = "2. 電子檔"

LANE_LABELS = {
    "第三方(Third Party)": "third_party",
    "部門(Department)": "department",
    "資訊系統(System)": "system",
    "產出資料與文件(Data/Report)": "data",
}

SYMBOL_GUIDE_ZH = """
# BIF / DFD 符號詞典（ISO 27701 個資盤點）

## 泳道（由上到下常見配色）
- 第三方 (Third Party)
- 部門 (Department)
- 資訊系統 (System)
- 產出資料與文件 (Data/Report)

## 資料資產（藍區）
格式：`NNN_檔案名稱(D)(P)`
- (D) = 電子檔
- (P) = 紙本
- 可同時出現 (D)(P)

## 箭頭標註
- `A:傳輸方式(保護措施)` — 可多個，以逗號分隔
  例：A:Email(X)、A:上傳(權限)、A:親送(專人)、A:登打(權限),列印(權限)
- `C:編號列表(型態)` — 此邊承載的資料檔
  例：C:002(D)、C:006,007(P)、C:007(D)(P)

## 保護措施括號
- (X) / ╳ → 無特別保護標示
- (權限) → 權限控管
- (專人) → 專人

## 展開成清冊列
每一「方向 × C 檔案 × A 傳輸方式 × 對應型態」→ Excel 一列：
A 檔案編號、B 作業流程名稱、G 檔案名稱、H 檔案型態、
AF From、AG To、AH 傳輸方式
"""
