// The resolve panel for a "needs you" card: what flow worked out on its own,
// the ranked candidates, a manual search, and the exact paths that confirming
// would create — previewed before anything is linked.

import { ignoreItem, previewItem, resolveItem, searchItem } from "./api.js";
import { el, formatBytes } from "./dom.js";

const dialog = document.getElementById("resolve");

function displayTitle(candidate) {
  const title = candidate.title_zh || candidate.title_en;
  return candidate.year ? `${title} (${candidate.year})` : title;
}

function poster(candidate, posterBase) {
  if (!candidate.poster_path) return el("div", { class: "poster none" }, "no poster");
  return el("img", {
    class: "poster",
    src: `${posterBase}${candidate.poster_path}`,
    alt: "",
    loading: "lazy",
  });
}

function commonDir(paths) {
  const split = paths.map((p) => p.split("/"));
  const prefix = [];
  for (let i = 0; i < split[0].length - 1; i++) {
    const part = split[0][i];
    if (split.every((s) => s[i] === part && i < s.length - 1)) prefix.push(part);
    else break;
  }
  return prefix.join("/") + "/";
}

function facts(item) {
  const parsedTitle = item.parsed_title_zh || item.parsed_title_en || "no title found";
  const kind = item.kind ?? "unknown";
  return el(
    "div",
    { class: "facts" },
    el("span", {}, `${item.video_count} video file${item.video_count === 1 ? "" : "s"} · ${formatBytes(item.total_bytes)}`),
    el("span", {}, `${item.classification_reason || "not scanned"} → `, el("strong", {}, kind)),
    el("span", {}, `title ${parsedTitle}${item.parsed_year ? ` · year ${item.parsed_year}` : ""}`),
  );
}

export function openResolve(item, meta, onChanged) {
  let candidates = item.candidates ?? [];
  let selected = candidates[0]?.tmdb_id ?? null;
  let previewToken = 0;
  let busy = false;

  const candidateList = el("div", { class: "cands" });
  const previewBox = el("div", { class: "prev" });
  const message = el("div", { class: "msg", role: "alert" });
  const confirmBtn = el("button", { class: "btn primary", type: "button" }, "Confirm & link");
  const ignoreBtn = el("button", { class: "btn", type: "button" }, "Not media — ignore");
  const laterBtn = el("button", { class: "btn", type: "button" }, "Leave for later");
  const searchBox = el("input", {
    class: "search",
    type: "search",
    placeholder: "None of these — search TMDB manually…",
    "aria-label": "Search TMDB",
  });

  function setMessage(text) {
    message.textContent = text ?? "";
  }

  function syncButtons() {
    confirmBtn.disabled = busy || selected === null || !previewBox.dataset.ready;
    ignoreBtn.disabled = busy;
  }

  function renderCandidates() {
    if (!candidates.length) {
      candidateList.replaceChildren(el("div", { class: "sub" }, "No TMDB matches. Try a manual search."));
      return;
    }
    candidateList.replaceChildren(
      ...candidates.map((c) =>
        el(
          "button",
          {
            type: "button",
            class: `cand${c.tmdb_id === selected ? " sel" : ""}`,
            "aria-pressed": c.tmdb_id === selected ? "true" : "false",
            onclick: () => select(c.tmdb_id),
          },
          poster(c, meta.poster_base),
          el(
            "div",
            { class: "cand-body" },
            el("div", { class: "t" }, displayTitle(c)),
            el("div", { class: "m" }, c.title_zh && c.title_en !== c.title_zh ? c.title_en : ""),
            el("div", { class: "m" }, c.reason),
          ),
          el("span", { class: `score${c.tmdb_id === selected ? " hi" : ""}` }, c.score.toFixed(2)),
        ),
      ),
    );
  }

  async function renderPreview() {
    const token = ++previewToken;
    delete previewBox.dataset.ready;
    syncButtons();
    if (selected === null) {
      previewBox.replaceChildren(el("div", { class: "sub" }, "Pick a match to see what will be created."));
      return;
    }
    previewBox.replaceChildren(el("div", { class: "sub" }, "Planning…"));
    try {
      const { paths } = await previewItem(item.hash, selected);
      if (token !== previewToken) return; // a newer selection won
      if (!paths.length) {
        previewBox.replaceChildren(el("div", { class: "sub" }, "Nothing would be linked for this match."));
        return;
      }
      const root = commonDir(paths);
      previewBox.replaceChildren(
        el("div", { class: "root" }, root),
        ...paths.map((p) => el("div", { class: "add" }, `└─ ${p.slice(root.length)}`)),
      );
      previewBox.dataset.ready = "1";
    } catch (error) {
      if (token !== previewToken) return;
      previewBox.replaceChildren(el("div", { class: "sub error" }, `Could not plan: ${error.message}`));
    }
    syncButtons();
  }

  function select(tmdbId) {
    selected = tmdbId;
    setMessage("");
    renderCandidates();
    renderPreview();
  }

  async function act(fn) {
    busy = true;
    syncButtons();
    setMessage("");
    try {
      await fn();
    } catch (error) {
      setMessage(error.message);
    } finally {
      busy = false;
      syncButtons();
    }
  }

  searchBox.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || !searchBox.value.trim()) return;
    event.preventDefault();
    act(async () => {
      const result = await searchItem(item.hash, searchBox.value.trim());
      candidates = result.candidates;
      selected = candidates[0]?.tmdb_id ?? null;
      renderCandidates();
      await renderPreview();
    });
  });

  confirmBtn.addEventListener("click", () =>
    act(async () => {
      const { state } = await resolveItem(item.hash, selected);
      await onChanged();
      if (state === "ready") dialog.close();
      else setMessage("flow could not link this. The card now says why.");
    }),
  );

  ignoreBtn.addEventListener("click", () =>
    act(async () => {
      await ignoreItem(item.hash);
      await onChanged();
      dialog.close();
    }),
  );

  laterBtn.addEventListener("click", () => dialog.close());

  dialog.replaceChildren(
    el(
      "div",
      { class: "hd" },
      el("div", { class: "src", id: "rp-name" }, item.name),
      facts(item),
      item.reason && el("div", { class: "why" }, `Stopped because: ${item.reason}`),
    ),
    el("div", { class: "sec" }, el("div", { class: "lbl" }, "TMDB matches"), candidateList, searchBox),
    el(
      "div",
      { class: "sec" },
      el("div", { class: "lbl" }, "What will be created"),
      previewBox,
      el("div", { class: "sub note" }, "Hardlinks only. The source file is never touched, and seeding continues."),
    ),
    el("div", { class: "sec actions" }, confirmBtn, ignoreBtn, laterBtn, message),
  );

  renderCandidates();
  renderPreview();
  dialog.showModal();
}
