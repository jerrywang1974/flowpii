# FlowPII

辨識 ISO 27701 個資盤點 **BIF/DFD 圖**（PDF／JPG／PNG），經人工覆核後匯出 **個人資料盤點清冊 Excel**。

## 功能

- Vision LLM（SpaceXAI / xAI）抽取圖中節點、資產、傳輸邊
- 規則引擎依 `A:`／`C:` 展開清冊列（自動填 A／B／G／H／AF／AG／AH）
- Web UI：上傳 → 預覽 → 編輯 → **確認後下載**
- 繁中／英文介面切換
- Docker Compose 一鍵部署

## 快速開始（本機）

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'

# 設定金鑰（勿 commit）
cp .env.example .env   # 或自行建立
# XAI_API_KEY=xai-...

# CLI：fixture 離線驗證
flowpii recognize samples/BIF圖\ DEMO.pdf -o out/demo.xlsx --fixture tests/fixtures/demo_graph.json

# CLI：真實 AI 辨識
flowpii recognize samples/BIF圖\ DEMO.pdf -o out/demo_ai.xlsx --json-out out/demo_ai.json

# Web
uvicorn flowpii.api:app --host 0.0.0.0 --port 8000
# 開啟 http://localhost:8000
```

## Docker

```bash
export XAI_API_KEY=xai-...
docker compose up --build
# http://localhost:8000
```

## 測試

```bash
pytest -q
```

## 範例

| samples | output |
|---|---|
| `BIF圖 DEMO.pdf` | `個人資料盤點清冊 DEMO.xlsx` |
| `BIF圖 支單DEMO.pdf` | `個人資料盤點清冊 支單DEMO.xlsx` |
| `BIF圖 韓國DEMO.pdf` | `個人資料盤點清冊 韓國DEMO.xlsx` |

## 安全

- API Key 只放 `.env`／環境變數，已列入 `.gitignore`
- 匯出前必須在 UI 按「確認並匯出」
