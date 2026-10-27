// The four-column board. Column keys and labels come from /api/meta; this file
// only knows which keys get which card treatment.

import { getBoard, getHealth, getMeta } from "./api.js";
import { basename, el } from "./dom.js";
import { openResolve } from "./resolve.js";

const POLL_MS = 4000;
// The checks themselves run every 5-15 minutes server-side; this only reads
// the recorded snapshot, so half a minute keeps the lights fresh for nothing.
const HEALTH_POLL_MS = 30000;

const boardNode = document.getElementById("board");
const statusNode = document.getElementById("status");
const healthNode = document.getElementById("health");

let meta = null;
let lastBoard = null;
let lastHealth = null;

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

// Every label and reason comes from the server, so a check added there needs
// no change here. The dot is colour only and hidden from screen readers; the
// status is said once, in visually hidden text after the label. A failing
// check's reason is said by its own red line below, so only the others carry
// their detail here.
function renderHealth(health) {
  const failing = health.checks.filter((check) => check.status === "fail");
  healthNode.replaceChildren(
    ...health.checks.map((check) =>
      el(
        "span",
        { class: "light", title: `${check.label}: ${check.status} — ${check.detail}` },
        el("span", { class: `dot dot-${check.status}`, "aria-hidden": "true" }),
        check.label,
        el(
          "span",
          { class: "sr-only" },
          check.status === "fail" ? `: ${check.status}` : `: ${check.status} — ${check.detail}`,
        ),
      ),
    ),
    ...failing.map((check) =>
      el("span", { class: "health-detail" }, `${check.label}: ${check.detail}`),
    ),
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
    setStatus(`can't reach nasflow: ${error.message}`, true);
  }
}

async function refreshHealth() {
  try {
    const health = await getHealth();
    // The checks run every 5-15 minutes, so most polls change nothing. Redrawing
    // anyway would hand assistive tech the same five lights twice a minute.
    const fingerprint = JSON.stringify(health);
    if (fingerprint === lastHealth) return;
    lastHealth = fingerprint;
    renderHealth(health);
  } catch {
    // Keep the last lights: the board poll owns the "can't reach nasflow" line.
  }
}

async function start() {
  try {
    meta = await getMeta();
  } catch (error) {
    setStatus(`can't reach nasflow: ${error.message}`, true);
    setTimeout(start, POLL_MS);
    return;
  }
  await refresh();
  setInterval(refresh, POLL_MS);
  await refreshHealth();
  setInterval(refreshHealth, HEALTH_POLL_MS);
}

start();
