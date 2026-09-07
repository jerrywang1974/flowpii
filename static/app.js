(() => {
  /**
   * FlowPII 前端狀態機。
   *
   * 連續上傳注意：
   * - 每次「開始辨識」都會產生新的 job_id / access_token（後端隔離）。
   * - 必須停止上一輪輪詢，並用 recognizeGeneration 丟棄過期的 poll 回呼，
   *   否則第一份結果可能在第二份辨識中途蓋掉畫面。
   * - 下載連結綁在當前 job；換檔後必須清空 downloadUrl。
   */
  const state = {
    lang: localStorage.getItem("flowpii_lang") || "zh-TW",
    i18n: {},
    file: null,
    jobId: null,
    accessToken: null,
    graph: null,
    rows: [],
    downloadUrl: null,
    pollTimer: null,
    /** 遞增序號：每次 runRecognize 開始時 +1；過期 poll 不得套用結果 */
    recognizeGeneration: 0,
  };

  const $ = (id) => document.getElementById(id);

  async function loadI18n(lang) {
    const res = await fetch(`/static/i18n/${lang}.json`);
    state.i18n = await res.json();
    state.lang = lang;
    localStorage.setItem("flowpii_lang", lang);
    applyI18n();
  }

  function t(key) {
    return state.i18n[key] || key;
  }

  function applyI18n() {
    const map = {
      "t-appTitle": "appTitle",
      "t-appSubtitle": "appSubtitle",
      "t-upload": "upload",
      "t-uploadHint": "uploadHint",
      "t-dropHere": "dropHere",
      "t-recognize": "recognize",
      "t-fixtureDemo": "fixtureDemo",
      "t-meta": "meta",
      "t-empty": "empty",
      "t-warnings": "warnings",
      "t-preview": "preview",
      "t-rows": "rows",
      "t-addRow": "addRow",
      "t-confirmExport": "confirmExport",
      "t-download": "download",
      "t-footer": "footer",
      "t-lang": "lang",
    };
    Object.entries(map).forEach(([id, key]) => {
      const el = $(id);
      if (el) el.textContent = t(key);
    });
    $("th-A").textContent = t("colA");
    $("th-B").textContent = t("colB");
    $("th-G").textContent = t("colG");
    $("th-H").textContent = t("colH");
    $("th-AF").textContent = t("colAF");
    $("th-AG").textContent = t("colAG");
    $("th-AH").textContent = t("colAH");
    document.documentElement.lang = state.lang === "en" ? "en" : "zh-Hant";
    renderRows();
  }

  function setStatus(msg, isError = false) {
    const el = $("status");
    el.textContent = msg || "";
    el.className = `mt-3 text-sm ${isError ? "text-red-600" : "text-slate-500"}`;
  }

  function renderMeta(meta) {
    const box = $("metaBox");
    if (!meta) {
      box.innerHTML = `<p id="t-empty" class="text-slate-400">${t("empty")}</p>`;
      return;
    }
    const items = [
      ["ID", meta.process_id],
      ["Process", meta.process_name],
      ["Dept", meta.department],
      ["BIF", meta.bif_version],
      ["Date", meta.inventory_date],
    ];
    box.innerHTML = items
      .map(
        ([k, v]) =>
          `<div class="flex justify-between gap-3"><dt class="text-slate-400">${k}</dt><dd class="font-medium text-right">${v || "—"}</dd></div>`
      )
      .join("");
  }

  function renderWarnings(warnings) {
    const box = $("warnBox");
    const list = $("warnList");
    if (!warnings || !warnings.length) {
      box.classList.add("hidden");
      list.innerHTML = "";
      return;
    }
    box.classList.remove("hidden");
    list.innerHTML = warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join("");
  }

  function escapeHtml(s) {
    return String(s)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function blankRow() {
    const meta = state.graph?.metadata || {};
    return {
      A: meta.process_id || "",
      B: meta.process_name || "",
      G: "",
      H: "2. 電子檔",
      AF: "",
      AG: "",
      AH: "",
    };
  }

  function renderRows() {
    const tbody = $("tbody");
    tbody.innerHTML = "";
    state.rows.forEach((row, idx) => {
      const tr = document.createElement("tr");
      tr.className = "align-top";
      const fields = ["A", "B", "G", "H", "AF", "AG", "AH"];
      fields.forEach((f) => {
        const td = document.createElement("td");
        td.className = "px-1 py-1";
        if (f === "H") {
          const sel = document.createElement("select");
          sel.className =
            "w-28 rounded border border-slate-200 px-1 py-1 text-xs bg-white";
          ["1. 紙本", "2. 電子檔"].forEach((opt) => {
            const o = document.createElement("option");
            o.value = opt;
            o.textContent = opt.startsWith("1") ? t("paper") : t("digital");
            if (row[f] === opt) o.selected = true;
            sel.appendChild(o);
          });
          sel.addEventListener("change", () => {
            state.rows[idx][f] = sel.value;
            invalidateDownload();
          });
          td.appendChild(sel);
        } else {
          const ta = document.createElement("textarea");
          ta.className =
            "w-full min-w-[6rem] rounded border border-slate-200 px-1.5 py-1 text-xs leading-snug";
          ta.value = row[f] || "";
          ta.rows = f === "AF" || f === "AG" ? 2 : 1;
          ta.addEventListener("input", () => {
            state.rows[idx][f] = ta.value;
            invalidateDownload();
          });
          td.appendChild(ta);
        }
        tr.appendChild(td);
      });
      const tdAct = document.createElement("td");
      tdAct.className = "px-1 py-1";
      const btn = document.createElement("button");
      btn.className = "text-xs text-red-600 hover:underline";
      btn.textContent = t("delete");
      btn.addEventListener("click", () => {
        state.rows.splice(idx, 1);
        invalidateDownload();
        renderRows();
      });
      tdAct.appendChild(btn);
      tr.appendChild(tdAct);
      tbody.appendChild(tr);
    });
  }

  function invalidateDownload() {
    state.downloadUrl = null;
    const btn = $("btnDownload");
    btn.disabled = true;
    $("successMsg").classList.add("hidden");
  }

  /**
   * 開始新一輪辨識前清空上一份結果，避免畫面殘留舊列／舊預覽／舊下載。
   */
  function resetJobUiForNewRecognize() {
    stopPoll();
    invalidateDownload();
    state.jobId = null;
    state.accessToken = null;
    state.graph = null;
    state.rows = [];
    renderRows();
    renderMeta(null);
    renderWarnings([]);
    $("previewImg").classList.add("hidden");
    $("previewImg").removeAttribute("src");
    $("previewEmpty").classList.remove("hidden");
    enableActions(false);
  }

  async function downloadExcelBlob(url) {
    setStatus(t("downloading"));
    const res = await fetch(url);
    if (!res.ok) {
      let detail = `download failed (HTTP ${res.status})`;
      try {
        const j = await res.json();
        if (j.detail) detail = j.detail;
      } catch {
        /* ignore */
      }
      throw new Error(detail);
    }
    const blob = await res.blob();
    const objectUrl = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = objectUrl;
    a.download = "個人資料盤點清冊.xlsx";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(objectUrl), 2000);
  }

  function enableActions(on) {
    $("btnAdd").disabled = !on;
    $("btnConfirm").disabled = !on;
  }

  function stopPoll() {
    if (state.pollTimer) {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  }

  function applyJobResult(data, generation) {
    // 過期回呼：使用者已開始下一輪辨識
    if (generation !== state.recognizeGeneration) return;
    if (data.job_id && data.job_id !== state.jobId) return;

    state.graph = data.graph;
    state.rows = data.rows || [];
    renderMeta(data.metadata);
    renderWarnings(data.warnings || []);
    renderRows();
    enableActions(true);
    if (data.preview_url) {
      $("previewImg").src = data.preview_url + "&t=" + Date.now();
      $("previewImg").classList.remove("hidden");
      $("previewEmpty").classList.add("hidden");
    }
    setStatus(`${(data.rows || []).length} rows · job ${state.jobId}`);
  }

  async function pollJobUntilDone(generation) {
    if (generation !== state.recognizeGeneration) return true; // abort quietly

    const expectedJobId = state.jobId;
    const token = state.accessToken;
    const url = `/api/jobs/${expectedJobId}?access_token=${encodeURIComponent(token)}`;
    const res = await fetch(url);
    const data = await res.json();

    if (generation !== state.recognizeGeneration) return true;
    if (!res.ok) {
      throw new Error(
        typeof data.detail === "string" ? data.detail : "poll failed"
      );
    }
    // 後端回傳的 job 必須仍是目前這輪
    if (data.job_id && data.job_id !== expectedJobId) {
      return true;
    }
    if (data.status === "queued" || data.status === "running") {
      setStatus(`${t("recognizing")} (${data.status}) · job ${expectedJobId}`);
      return false;
    }
    if (data.status === "error") {
      throw new Error(data.error || "recognize failed");
    }
    applyJobResult(data, generation);
    return true;
  }

  async function runRecognize({ fixture = null } = {}) {
    const btn = $("btnRecognize");
    btn.disabled = true;
    $("t-recognize").textContent = t("recognizing");

    // 換檔／連續辨識：先清狀態並作廢上一輪 poll
    resetJobUiForNewRecognize();
    const generation = ++state.recognizeGeneration;
    setStatus(t("recognizing"));

    try {
      const fd = new FormData();
      if (fixture) {
        fd.append(
          "file",
          new Blob(["fixture"], { type: "application/pdf" }),
          "demo.pdf"
        );
      } else {
        if (!state.file) throw new Error("No file");
        fd.append("file", state.file);
      }
      const url = fixture
        ? `/api/recognize?use_fixture=${encodeURIComponent(fixture)}`
        : "/api/recognize";
      const res = await fetch(url, { method: "POST", body: fd });
      let data;
      try {
        data = await res.json();
      } catch {
        throw new Error(`recognize failed (HTTP ${res.status})`);
      }
      if (!res.ok) {
        const detail =
          typeof data.detail === "string"
            ? data.detail
            : JSON.stringify(data.detail || data);
        throw new Error(detail || "recognize failed");
      }

      if (generation !== state.recognizeGeneration) return;

      state.jobId = data.job_id;
      state.accessToken = data.access_token;
      setStatus(`${t("recognizing")} · job ${state.jobId}`);

      const done = await pollJobUntilDone(generation);
      if (!done) {
        await new Promise((resolve, reject) => {
          state.pollTimer = setInterval(async () => {
            try {
              if (await pollJobUntilDone(generation)) {
                stopPoll();
                resolve();
              }
            } catch (err) {
              stopPoll();
              reject(err);
            }
          }, 2000);
        });
      }
    } catch (err) {
      if (generation === state.recognizeGeneration) {
        stopPoll();
        setStatus(String(err.message || err), true);
        enableActions(false);
      }
    } finally {
      if (generation === state.recognizeGeneration) {
        $("t-recognize").textContent = t("recognize");
        btn.disabled = !(state.file || fixture);
      }
    }
  }

  async function confirmExport() {
    if (!state.jobId || !state.accessToken) return;
    const btn = $("btnConfirm");
    btn.disabled = true;
    $("t-confirmExport").textContent = t("exporting");
    setStatus(t("exporting"));
    try {
      const res = await fetch(`/api/jobs/${state.jobId}/confirm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          rows: state.rows,
          graph: state.graph,
          access_token: state.accessToken,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setStatus(
          typeof data.detail === "string" ? data.detail : "confirm failed",
          true
        );
        return;
      }
      state.downloadUrl = data.download_url;
      $("btnDownload").disabled = false;
      const msg = $("successMsg");
      msg.textContent = t("success");
      msg.classList.remove("hidden");
      setStatus(`${data.row_count} rows · ${t("downloading")}`);
      await downloadExcelBlob(data.download_url);
      setStatus(`${data.row_count} rows · ${t("downloadReady")}`);
    } catch (err) {
      setStatus(String(err.message || err), true);
    } finally {
      $("t-confirmExport").textContent = t("confirmExport");
      btn.disabled = false;
    }
  }

  // events
  $("lang").value = state.lang;
  $("lang").addEventListener("change", (e) => loadI18n(e.target.value));

  $("file").addEventListener("change", (e) => {
    const f = e.target.files?.[0];
    state.file = f || null;
    $("fileName").textContent = f ? f.name : "";
    $("btnRecognize").disabled = !f;
    // 選了新檔但尚未辨識：清掉上一份下載，避免誤下舊檔
    invalidateDownload();
  });

  const dz = $("dropzone");
  ["dragenter", "dragover"].forEach((ev) =>
    dz.addEventListener(ev, (e) => {
      e.preventDefault();
      dz.classList.add("border-brand-500");
    })
  );
  ["dragleave", "drop"].forEach((ev) =>
    dz.addEventListener(ev, (e) => {
      e.preventDefault();
      dz.classList.remove("border-brand-500");
    })
  );
  dz.addEventListener("drop", (e) => {
    const f = e.dataTransfer.files?.[0];
    if (!f) return;
    state.file = f;
    $("fileName").textContent = f.name;
    $("btnRecognize").disabled = false;
    invalidateDownload();
  });

  $("btnRecognize").addEventListener("click", () => runRecognize());
  $("btnFixture").addEventListener("click", () =>
    runRecognize({ fixture: "demo_graph.json" })
  );
  $("btnAdd").addEventListener("click", () => {
    state.rows.push(blankRow());
    invalidateDownload();
    renderRows();
  });
  $("btnConfirm").addEventListener("click", confirmExport);
  $("btnDownload").addEventListener("click", async () => {
    if (!state.downloadUrl) {
      setStatus(t("needConfirm"), true);
      return;
    }
    try {
      await downloadExcelBlob(state.downloadUrl);
      setStatus(t("downloadReady"));
    } catch (err) {
      setStatus(String(err.message || err), true);
    }
  });

  loadI18n(state.lang).then(() => renderMeta(null));
})();
