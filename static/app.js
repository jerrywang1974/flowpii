(() => {
  const state = {
    lang: localStorage.getItem("flowpii_lang") || "zh-TW",
    i18n: {},
    file: null,
    jobId: null,
    graph: null,
    rows: [],
    downloadUrl: null,
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
    const a = $("btnDownload");
    a.href = "#";
    a.classList.add("pointer-events-none", "opacity-40");
    $("successMsg").classList.add("hidden");
  }

  function enableActions(on) {
    $("btnAdd").disabled = !on;
    $("btnConfirm").disabled = !on;
  }

  async function runRecognize({ fixture = null } = {}) {
    const btn = $("btnRecognize");
    btn.disabled = true;
    $("t-recognize").textContent = t("recognizing");
    setStatus(t("recognizing"));
    invalidateDownload();

    try {
      const fd = new FormData();
      if (fixture) {
        // Upload a tiny placeholder; server uses fixture
        fd.append("file", new Blob(["fixture"], { type: "application/pdf" }), "demo.pdf");
      } else {
        if (!state.file) throw new Error("No file");
        fd.append("file", state.file);
      }
      const url = fixture
        ? `/api/recognize?use_fixture=${encodeURIComponent(fixture)}`
        : "/api/recognize";
      const res = await fetch(url, { method: "POST", body: fd });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "recognize failed");

      state.jobId = data.job_id;
      state.graph = data.graph;
      state.rows = data.rows || [];
      renderMeta(data.metadata);
      renderWarnings(data.warnings);
      renderRows();
      enableActions(true);

      if (data.preview_url) {
        $("previewImg").src = data.preview_url + "?t=" + Date.now();
        $("previewImg").classList.remove("hidden");
        $("previewEmpty").classList.add("hidden");
      }
      setStatus(`${data.rows.length} rows · job ${data.job_id}`);
    } catch (err) {
      setStatus(String(err.message || err), true);
      enableActions(false);
    } finally {
      $("t-recognize").textContent = t("recognize");
      btn.disabled = !state.file;
    }
  }

  async function confirmExport() {
    if (!state.jobId) return;
    setStatus("…");
    const res = await fetch(`/api/jobs/${state.jobId}/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rows: state.rows, graph: state.graph }),
    });
    const data = await res.json();
    if (!res.ok) {
      setStatus(data.detail || "confirm failed", true);
      return;
    }
    state.downloadUrl = data.download_url;
    const a = $("btnDownload");
    a.href = data.download_url;
    a.classList.remove("pointer-events-none", "opacity-40");
    const msg = $("successMsg");
    msg.textContent = t("success");
    msg.classList.remove("hidden");
    setStatus(`${data.row_count} rows confirmed`);
  }

  // events
  $("lang").value = state.lang;
  $("lang").addEventListener("change", (e) => loadI18n(e.target.value));

  $("file").addEventListener("change", (e) => {
    const f = e.target.files?.[0];
    state.file = f || null;
    $("fileName").textContent = f ? f.name : "";
    $("btnRecognize").disabled = !f;
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

  loadI18n(state.lang).then(() => renderMeta(null));
})();
