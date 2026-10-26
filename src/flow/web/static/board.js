// The four-column board. Column keys and labels come from /api/meta; this file
// only knows which keys get which card treatment.

import { getBoard, getMeta } from "./api.js";
import { basename, el } from "./dom.js";
import { openResolve } from "./resolve.js";

const POLL_MS = 4000;

const boardNode = document.getElementById("board");
const statusNode = document.getElementById("status");

let meta = null;
let lastBoard = null;

function cardSubtitle(column, item) {
  switch (column) {
    case "downloaded":
      return "waiting to be identified";
    case "identifying":
      return "looking it up…";
    case "needs_you":
      return item.reason || "needs a decision";
    case "ready":
      return item.dest_root ? `linked → ${item.dest_root}` : "linked";
    default:
      return "";
  }
}

function cardTitle(column, item) {
  // Once filed, the Plex-style folder name is what the human will look for.
  return column === "ready" && item.dest_root ? basename(item.dest_root) : item.name;
}

function renderCard(column, item) {
  const actionable = column === "needs_you";
  return el(
    "div",
    { class: `card${actionable ? " actionable" : ""}`, title: item.name },
    el("div", { class: "card-title" }, cardTitle(column, item)),
    el("div", { class: "sub" }, cardSubtitle(column, item)),
    actionable &&
      el(
        "button",
        { class: "btn primary", type: "button", onclick: () => openResolve(item, meta, refresh) },
        "Choose…",
      ),
  );
}

function render(board) {
  boardNode.replaceChildren(
    ...meta.columns.map(({ key, label }) => {
      const items = board[key] ?? [];
      const heading = items.length ? `${label} · ${items.length}` : label;
      return el(
        "section",
        { class: `col col-${key}`, "aria-label": label },
        el("h2", { class: "colh" }, heading),
        items.length
          ? items.map((item) => renderCard(key, item))
          : el("div", { class: "empty" }, "—"),
      );
    }),
  );
}

function setStatus(text, isError = false) {
  statusNode.textContent = text;
  statusNode.classList.toggle("error", isError);
}

async function refresh() {
  try {
    const board = await getBoard();
    lastBoard = board;
    render(board);
    setStatus(`updated ${new Date().toLocaleTimeString()}`);
  } catch (error) {
    // Keep showing the last good board rather than blanking it.
    if (lastBoard) render(lastBoard);
    setStatus(`can't reach flow: ${error.message}`, true);
  }
}

async function start() {
  try {
    meta = await getMeta();
  } catch (error) {
    setStatus(`can't reach flow: ${error.message}`, true);
    setTimeout(start, POLL_MS);
    return;
  }
  await refresh();
  setInterval(refresh, POLL_MS);
}

start();
