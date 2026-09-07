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

## 多人同時使用

目前設計支援**同站台、不同人／不同地點同時上傳辨識**（單機 uvicorn）：

| 能力 | 說明 |
|---|---|
| 工作隔離 | 每次上傳產生獨立 `job_id` 與 `uploads/{job_id}/` 目錄 |
| 非同步辨識 | 上傳後立即回傳，背景執行 AI；前端輪詢狀態 |
| 存取權杖 | `access_token` 僅回傳給上傳者；確認／下載需帶此 token |
| 併發上限 | 預設同時最多 2 件 AI 辨識（`FLOWPII_MAX_CONCURRENT`） |
| 落盤 | `job.json` 持久化，重啟後仍可查詢未完成工作 |

**尚未包含**：登入帳號、跨多台機器的共用佇列（Redis／Celery）、企業 SSO。若要正式對外開放，建議再加帳密或 SSO。

## 安全

- API Key 只放 `.env`／環境變數，已列入 `.gitignore`
- 匯出前必須在 UI 按「確認並匯出」
- 下載／確認需 `access_token`（避免他人猜到 job_id 就下載）
