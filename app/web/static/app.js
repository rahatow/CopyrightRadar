/* CopyrightRadar main page. Data from /api/state, texts from /i18n/<lang>.json. */
(() => {
  "use strict";
  const root = document.getElementById("app");
  const LANG = root.dataset.lang || "de";
  const LOCALE = { de: "de-DE", en: "en-GB", ru: "ru-RU" }[LANG];
  const ORDER = ["high", "medium", "unknown", "low", "no_music", "no_audio"];
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fill = (tpl, v) => String(tpl).replace(/\{(\w+)\}/g, (_, k) => esc(v[k] ?? "?"));
  const fmtDate = (iso, time) => {
    if (!iso) return "";
    try {
      return new Date(iso).toLocaleString(LOCALE, time
        ? { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }
        : { day: "2-digit", month: "2-digit", year: "numeric" });
    } catch (e) { return iso; }
  };
  let T = {}, S = null, tab = "feed", filter = null, manualOnly = false;
  try { const v = localStorage.getItem("cr-tab"); if (v === "archive") tab = v; } catch (e) {}

  const archived = (m) => m.state !== "in_feed";
  const L = (lv) => T.levels[lv] || [lv, ""];

  async function api(path, body, isForm) {
    try {
      const r = await fetch(path, {
        method: "POST", credentials: "same-origin",
        headers: isForm ? {} : { "Content-Type": "application/json" },
        body: isForm ? body : JSON.stringify(body || {}),
      });
      let j = null; try { j = await r.json(); } catch (e) {}
      return { ok: r.ok, status: r.status, body: j };
    } catch (e) { return { ok: false, body: null }; }
  }
  const errText = (b) => (b && T.err[b.error]) || (b && b.error) || T.err.net;
  function say(text, err) { const m = $("msg"); m.className = "msg" + (err ? " err" : ""); m.innerHTML = text; }

  async function load() {
    const r = await fetch("/api/state", { credentials: "same-origin", cache: "no-store" });
    if (r.status === 401) { location.href = "/login"; return; }
    if (!r.ok) throw new Error("state");
    S = await r.json();
    render();
  }

  function render() {
    const acc = S.account || {};
    $("handle").textContent = acc.username ? "@" + acc.username : T.title;
    $("avatar").textContent = (acc.username || "C").slice(0, 1).toUpperCase();
    const feed = S.media.filter((m) => !archived(m));
    const arch = S.media.filter(archived);
    const notes = [];
    if (S.demo) notes.push(T.demo);
    if (!S.instagram_configured && !S.demo) notes.push(T.err.no_token);
    if (S.instagram_configured && S.copyright_supported === false) notes.push(T.no_meta_field);
    if (!S.acr_configured) notes.push(T.no_acr);
    $("notices").innerHTML = notes.map((n) => `<p class="notice">${esc(n)}</p>`).join("");
    $("refresh-info").textContent = S.last_refresh ? fill(T.last_check, { at: fmtDate(S.last_refresh.at, true) }) : T.never_checked;
    renderHero(feed, arch);
    $("jobs").textContent = S.jobs && S.jobs.pending > 0 ? fill(T.jobs, S.jobs) : "";

    const tabList = tab === "archive" ? arch : feed;
    const manualCount = tabList.filter((m) => m.manual_check.length).length;
    if (!manualCount) manualOnly = false;
    const list = manualOnly ? tabList.filter((m) => m.manual_check.length) : tabList;
    const counts = {}; list.forEach((m) => (counts[m.level] = (counts[m.level] || 0) + 1));
    if (filter && !counts[filter]) filter = null;
    $("chips").innerHTML = `<button class="chip" data-r="" aria-pressed="${!filter}">${esc(T.all)} <b>${list.length}</b></button>` +
      ORDER.filter((r) => counts[r]).map((r) =>
        `<button class="chip lv-${r}" data-r="${r}" aria-pressed="${filter === r}"><span class="dot"></span>${esc(L(r)[0])} <b>${counts[r]}</b></button>`).join("");
    if (manualCount) $("chips").innerHTML += `<button class="chip manual" id="chip-manual" aria-pressed="${manualOnly}">${esc(T.manual_chip)} <b>${manualCount}</b></button>`;
    $("chips").querySelectorAll(".chip[data-r]").forEach((b) => (b.onclick = () => { filter = b.dataset.r || null; render(); }));
    const mc = $("chip-manual"); if (mc) mc.onclick = () => { manualOnly = !manualOnly; render(); };

    const shown = list.filter((m) => !filter || m.level === filter);
    $("grid").innerHTML = shown.map((m) => {
      const img = m.thumb ? `<img src="${esc(m.thumb)}" alt="" loading="lazy">` : `<div class="ph">${esc(m.caption || "—")}</div>`;
      const flags = [
        m.kind === "photo" ? T.flag.photo : "",
        !m.in_api ? T.flag.export : "",
        m.state === "missing" ? T.flag.missing : "",
        m.state === "archived_manual" ? T.flag.manual : "",
        m.manual_check.length ? T.flag.check : "",
      ].filter(Boolean).map((f) => `<span class="flag">${esc(f)}</span>`).join("");
      return `<button class="tile" data-id="${esc(m.id)}" aria-label="${esc(fmtDate(m.timestamp) + ", " + L(m.level)[0])}">${img}` +
        `<span class="badge lv-${esc(m.level)}"><span class="dot"></span>${esc(L(m.level)[0])}</span><span class="flags">${flags}</span>` +
        `<span class="date">${esc(fmtDate(m.timestamp))}</span></button>`;
    }).join("");
    $("empty").hidden = shown.length > 0;
    $("grid").querySelectorAll(".tile").forEach((t) => (t.onclick = () => open(S.media.find((m) => m.id === t.dataset.id))));
    $("archive-panel").hidden = tab !== "archive";
    $("last-import").textContent = S.last_import ? fill(T.last_import, { at: fmtDate(S.last_import.at, true), ...S.last_import }) : T.never_imported;
    $("tab-feed").setAttribute("aria-selected", tab === "feed");
    $("tab-archive").setAttribute("aria-selected", tab === "archive");
    $("legend").innerHTML = ORDER.map((r) => `<div class="lv-${r}"><span class="dot"></span><span><b>${esc(L(r)[0])}.</b> ${esc(L(r)[1])}</span></div>`).join("");
  }

  function renderHero(feed, arch) {
    const vids = S.media.filter((m) => m.kind === "video");
    const ofHtml = (a, b) => `${a} <span class="of">${esc(T.of)} ${b}</span>`;
    const recognised = vids.filter((m) => m.acr.some((a) => a.status === "match")).length;
    const metaDone = vids.filter((m) => m.meta.length && ["complete", "completed"].includes(m.meta[0].status)).length;
    const manual = S.media.filter((m) => m.manual_check.length).length;
    const minis = [
      ["high", ofHtml(feed.filter((m) => m.level === "high").length, feed.length), () => { tab = "feed"; filter = "high"; manualOnly = false; }],
      ["manual", ofHtml(manual, S.media.length), () => { manualOnly = true; filter = null; }],
      ["recognised", ofHtml(recognised, vids.length), () => {}],
      ["meta", ofHtml(metaDone, vids.length), () => {}],
      ["archive", String(arch.length), () => { tab = "archive"; filter = null; manualOnly = false; }],
    ];
    $("minis").innerHTML = minis.map(([k, v], i) => `<button class="mini" data-k="${k}" type="button"><span class="q"><span class="num">${i + 1}</span>${esc(T.mini[k][0])}</span>` +
      `<span class="big">${v}</span><span class="sub">${esc(T.mini[k][1])}</span></button>`).join("");
    $("minis").querySelectorAll(".mini").forEach((b, i) => (b.onclick = () => { minis[i][2](); save(); render(); $("posts").scrollIntoView({ behavior: "smooth" }); }));
    const acc = S.account || {};
    $("hero-title").textContent = fill(T.hero_title, { n: vids.length });
    $("hero-meta").innerHTML = [acc.username ? `<b>@${esc(acc.username)}</b>` : "", esc(S.acr_configured ? T.hero_meta_acr_on : T.hero_meta_acr_off),
      esc(fill(T.hero_meta_meta, { state: S.copyright_supported ? T.meta_on : T.meta_off }))].filter(Boolean).map((x) => `<span>${x}</span>`).join("");
  }

  function reasonText(r) {
    const tpl = T.reasons[r.k] || r.k;
    const v = { ...r };
    if (v.meta_status) v.meta_status = (T.meta_status[v.meta_status] || v.meta_status);
    if (!v.meta_status && "meta_status" in r) v.meta_status = "—";
    return fill(tpl, v);
  }
  const row = (k, v) => (v ? `<dt>${esc(k)}</dt><dd>${v}</dd>` : "");

  function open(m) {
    if (!m) return;
    const d = $("dlg"), R = T.rows;
    const pic = m.thumb ? `<img src="${esc(m.thumb)}" alt="">` : `<div class="ph">${esc(m.caption || "")}</div>`;
    const acr = m.acr.length ? "<ul>" + m.acr.map((a) => `<li>${esc(Math.round(a.segment_start_s || 0))} s: ` +
      (a.status === "match" ? `${esc(a.artists || "?")} – ${esc(a.title || "?")} [${esc(a.label || "—")}], ${esc(T.score)} ${esc(a.score ?? "?")}${a.isrc ? ", ISRC " + esc(a.isrc) : ""}` : esc(a.status === "no_match" ? T.no_match : T.reasons.acr_error)) +
      "</li>").join("") + "</ul>" : "";
    const meta = m.meta.length ? "<ul>" + m.meta.map((x) => `<li>${esc(fmtDate(x.at))}: ${esc(T.meta_status[x.status] || x.status || "—")}` +
      x.matches.map((mm) => `<br>${esc(mm.owner_copyright_policy || mm.author || "?")}${mm.title ? " · " + esc(mm.title) : ""}`).join("") + "</li>").join("") + "</ul>" : "";
    let audio = "";
    if (m.kind === "video") {
      audio = m.audio ? esc(T.audio[m.audio.kind] || m.audio.kind) + (m.audio.has_audio_stream === 0 ? " · " + esc(T.audio.no_stream) : "") +
        (m.audio.has_audio_stream && m.audio.playable ? `<audio controls preload="none" src="/media/audio/${encodeURIComponent(m.id)}"></audio>` : "") : esc(T.audio.none);
    }
    const state = esc(fill(T.state[m.state] || m.state, { since: fmtDate(m.since, true) }));
    const review = m.review ? esc(T.review[m.review.verdict]) + (m.review.note ? " · " + esc(m.review.note) : "") : "";
    const markBtn = m.state === "in_feed" ? `<button class="btn ghost" id="d-mark">${esc(T.mark_on)}</button>`
      : m.state === "archived_manual" ? `<button class="btn ghost" id="d-unmark">${esc(T.mark_off)}</button>` : "";
    d.innerHTML = `<div class="sheet"><div class="pic">${pic}</div><div class="info">
      <div class="head"><span class="pillv lv-${esc(m.level)}"><span class="dot"></span>${esc(L(m.level)[0])}</span>
      <button class="close" id="d-close" aria-label="${esc(T.close)}">×</button></div>
      <p class="todo">${esc(T.todo[m.level] || "")}</p>
      ${m.manual_check.length ? `<div class="notice"><strong>${esc(T.manual_h)}</strong><ul>${m.manual_check.map((k) => `<li>${esc(T.manual[k] || k)}</li>`).join("")}</ul></div>` : ""}
      <dl>
        ${row(R.why, "<ul>" + m.reasons.map((r) => `<li>${reasonText(r)}</li>`).join("") + "</ul>")}
        ${row(R.date, esc(fmtDate(m.timestamp, true)))}
        ${row(R.state, state)}
        ${row(R.acr, acr)}
        ${row(R.meta, meta)}
        ${row(R.audio, audio)}
        ${row(R.review, review)}
        ${row(R.link, m.permalink ? `<a href="${esc(m.permalink)}" target="_blank" rel="noopener noreferrer">${esc(T.open_ig)}</a>` : "")}
        ${row(R.caption, m.caption ? esc(m.caption) : "")}
      </dl>
      <div class="row">${markBtn}${m.kind === "video" ? `<button class="btn ghost" id="d-analyze">${esc(T.analyze)}</button>
        <label class="btn ghost file">${esc(T.upload)}<input type="file" id="d-upload" accept="video/*,audio/*" hidden></label>` : ""}</div>
      <div class="stack"><strong>${esc(T.review_h)}</strong>
        <div class="row">${Object.keys(T.review).map((k) => `<button class="btn ${m.review && m.review.verdict === k ? "" : "ghost"}" data-v="${k}">${esc(T.review[k])}</button>`).join("")}
        ${m.review ? `<button class="btn ghost" data-v="">${esc(T.review_clear)}</button>` : ""}</div>
        <label>${esc(T.note)}<input id="d-note" maxlength="1000" value="${esc(m.review ? m.review.note || "" : "")}"></label>
      </div>
      <p class="msg" id="d-msg" role="status"></p>
    </div></div>`;
    const dmsg = (t, err) => { const x = d.querySelector("#d-msg"); x.className = "msg" + (err ? " err" : ""); x.textContent = t; };
    const after = async (r) => { if (r.ok) { await load(); open(S.media.find((x) => x.id === m.id)); } else dmsg(T.not_saved + errText(r.body), true); };
    d.querySelector("#d-close").onclick = () => d.close();
    const mk = d.querySelector("#d-mark"); if (mk) mk.onclick = async () => after(await api("/api/mark", { id: m.id, archived: true }));
    const um = d.querySelector("#d-unmark"); if (um) um.onclick = async () => after(await api("/api/mark", { id: m.id, archived: false }));
    const an = d.querySelector("#d-analyze"); if (an) an.onclick = async () => { const r = await api("/api/analyze", { ids: [m.id] }); dmsg(r.ok ? fill(T.jobs, { pending: r.body.queued }) : T.not_saved + errText(r.body), !r.ok); };
    const up = d.querySelector("#d-upload");
    if (up) up.onchange = async () => { if (!up.files[0]) return; const f = new FormData(); f.append("file", up.files[0]); after(await api("/api/upload/" + encodeURIComponent(m.id), f, true)); };
    d.querySelectorAll("[data-v]").forEach((b) => (b.onclick = async () =>
      after(await api("/api/review", { id: m.id, verdict: b.dataset.v || null, note: d.querySelector("#d-note").value }))));
    if (!d.open) d.showModal();
  }

  $("legend-toggle").onclick = () => {
    const box = $("legend-box"); box.hidden = !box.hidden;
    $("legend-toggle").setAttribute("aria-expanded", String(!box.hidden));
  };
  $("dlg").addEventListener("click", (e) => { if (e.target.id === "dlg") e.target.close(); });
  $("tab-feed").onclick = () => { tab = "feed"; filter = null; save(); render(); };
  $("tab-archive").onclick = () => { tab = "archive"; filter = null; save(); render(); };
  function save() { try { localStorage.setItem("cr-tab", tab); } catch (e) {} }

  $("refresh").onclick = async () => {
    const b = $("refresh"); b.disabled = true; b.textContent = T.checking; say("");
    let r = await api("/api/refresh");
    if (!r.ok && r.body && r.body.error === "too_many_missing" && Number.isInteger(r.body.missing) &&
        confirm(fill(T.confirm_mass, r.body))) {
      r = await api("/api/refresh", { confirm: r.body.missing });
    }
    b.disabled = false; b.textContent = T.refresh;
    if (r.ok) { await load(); say(fill(T.result, r.body) + (r.body.still_visible_manual ? fill(T.still_visible, r.body) : "")); }
    else say(esc(errText(r.body)), true);
  };
  $("analyze-all").onclick = async () => {
    const r = await api("/api/analyze", { ids: "all" });
    say(r.ok ? fill(T.jobs, { pending: r.body.queued }) : esc(errText(r.body)), !r.ok);
    poll();
  };
  const importZip = async (inp) => {
    if (!inp.files[0]) return;
    say(esc(T.importing));
    const f = new FormData(); f.append("file", inp.files[0]);
    const r = await api("/api/import", f, true);
    inp.value = "";
    if (r.ok) { await load(); say(fill(T.import_done, r.body)); poll(); } else say(esc(errText(r.body)), true);
  };
  $("import").onchange = () => importZip($("import"));
  $("import-archive").onchange = () => importZip($("import-archive"));

  let polling = false;
  async function poll() {
    if (polling) return; polling = true;
    while (true) {
      await new Promise((res) => setTimeout(res, 4000));
      try { await load(); } catch (e) { break; }
      if (!S.jobs || S.jobs.pending <= 0) break;
    }
    polling = false;
  }

  fetch(`/i18n/${LANG}.json`).then((r) => r.json()).then(async (t) => {
    T = t;
    try { await load(); if (S.jobs && S.jobs.pending > 0) poll(); }
    catch (e) { const em = $("empty"); em.textContent = T.load_err; em.hidden = false; }
  });
})();
