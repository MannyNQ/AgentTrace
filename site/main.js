// Draws the interlocking diagram from two real traces of the demo agent (site/proof/trace-d5d3dd53….jsonl
// for main, trace-6696a708….jsonl for the PR), sets routes from the NX buttons, reads any berth into
// the describer, and makes the copy buttons work. Without this file the page still reads: the
// describer shows the points and a text list carries both runs.

// What each berth (one span) shows and reads out. Positions live in the layouts below.
const BERTHS = [
  { id: "Q", text: "“1044?”", routes: ["main", "pr"], head: "Berth Q · the question",
    read: "support_agent, both runs · input “Can I get a refund on order 1044?” · main 5.11 s, $0.0145 · PR 4.69 s, $0.0139" },
  { id: "P12", text: "llm 2.14|1.66", routes: ["main", "pr"], head: "Berth P12 · points",
    read: 'The first LLM call picks the tools. main: lookup_order(order_id="1044"), then search_kb · PR: search_kb(query="refund policy") only · 2.14 s · 1.66 s' },
  { id: "M1", text: "lookup_order", routes: ["main"], head: "Berth M1 · main · tool span",
    read: 'lookup_order(order_id="1044") · ok · status "delivered", delivered_on "2026-08-01". The step the PR skipped.' },
  { id: "M4", text: "search_kb", routes: ["main"], head: "Berth M4 · main · tool span",
    read: 'search_kb(query="refund policy") · ok. main reads the policy too, but only after it has checked the order.' },
  { id: "M2", text: "llm 2.96 s", routes: ["main"], head: "Berth M2 · main · llm span",
    read: "anthropic.messages.create · claude-opus-5-5 · 2.96 s · 1,309 → 169 tokens · $0.0086" },
  { id: "M3", text: "can’t refund", routes: ["main"], head: "Berth M3 · main · answer",
    read: "“I’m sorry, but I can’t refund order 1044. It was delivered on August 1, 2026, about 90 days ago…” · answer pass · steps pass" },
  { id: "R1", text: "search_kb", routes: ["pr"], head: "Berth R1 · PR · tool span",
    read: 'search_kb(query="refund policy") · ok · [{"id": "refund-policy", …}]. A real tool, the wrong step: the policy can’t say when 1044 was delivered.' },
  { id: "R2", text: "llm 3.03 s", routes: ["pr"], head: "Berth R2 · PR · llm span",
    read: "anthropic.messages.create · claude-opus-5-5 · 3.03 s · 1,139 → 208 tokens · $0.0087" },
  { id: "R3", text: "“it qualifies”", routes: ["pr"], head: "Berth R3 · PR · answer",
    read: "“Under our refund policy, delivered orders can be refunded within 30 days of delivery, so order 1044 qualifies if it arrived on or after October 1, 2026. …” · answer pass · steps FAIL" },
];

// Track circuits light for the routes listed; the 6px breaks between them are the insulated joints.
const ROUTES = { T101: "main pr", T102: "main pr", T103: "pr", T104: "main", T110: "main", T105: "main", T106: "main", T107: "pr", T108: "pr", T109: "pr" };

// The same interlocking drawn two ways: along the screen when it's wide, down it when it's narrow.
const WIDE = (() => {
  const M = 104, L = 244;
  return {
    w: 1440, h: 360, bw: 148, bh: 34, font: 13,
    track: {
      T101: `M40 ${M} H300`, T102: `M306 ${M} H560`, T103: `M566 ${M} L706 ${L}`,
      T104: `M566 ${M} H760`, T110: `M766 ${M} H920`, T105: `M926 ${M} H1080`, T106: `M1086 ${M} H1240`,
      T107: `M712 ${L} H930`, T108: `M936 ${L} H1090`, T109: `M1096 ${L} H1240`,
    },
    onward: [`M1246 ${M} H1440`, `M1246 ${L} H1440`],
    points: [560, M],
    // [centre x, top y, tick from, tick to]: the tick joins the berth to its track.
    berths: {
      Q: [170, M - 70], P12: [433, M - 70], M1: [670, M - 70], M4: [843, M - 70], M2: [1003, M - 70], M3: [1160, M - 70],
      R1: [820, L + 40], R2: [1010, L + 40], R3: [1170, L + 40],
    },
    tick: (b, [x, y]) => (y < M ? [x, y + 34, x, M - 8] : [x, L + 8, x, y]),
    signals: [{ x: 1252, y: M, up: true, ok: true, label: "S1" }, { x: 1252, y: L, up: false, ok: false, label: "S2" }],
    plates: [[40, M + 26, "Up main · main @ bd0e24a", 196], [304, M + 26, "P12 points · reversed by the PR", 232], [712, L + 84, "PR loop · prompt-change @ f15af57", 244]],
    labels: [[40, L + 36, "Support_agent Jn. · Diagram 1044"], [40, L + 56, "Track circuits T101–T110"]],
  };
})();

const NARROW = (() => {
  const M = 150, L = 310;
  return {
    w: 390, h: 760, bw: 128, bh: 30, font: 12,
    track: {
      T101: `M${M} 20 V70`, T102: `M${M} 76 V180`, T103: `M${M} 186 L${L} 346`,
      T104: `M${M} 186 V300`, T110: `M${M} 306 V410`, T105: `M${M} 416 V520`, T106: `M${M} 526 V630`,
      T107: `M${L} 352 V450`, T108: `M${L} 456 V550`, T109: `M${L} 556 V630`,
    },
    onward: [`M${M} 636 V760`, `M${L} 636 V760`],
    points: [M, 180],
    berths: {
      Q: [70, 30], P12: [70, 118], M1: [70, 228], M4: [70, 330], M2: [70, 448], M3: [70, 568],
      R1: [230, 386], R2: [230, 488], R3: [230, 578],
    },
    tick: (b, [x, y]) => (b.id.startsWith("R") ? [x + 64, y + 15, L - 8, y + 15] : [x + 64, y + 15, M - 8, y + 15]),
    signals: [{ x: M - 13, y: 642, down: true, ok: true, label: "S1" }, { x: L - 13, y: 642, down: true, ok: false, label: "S2" }],
    plates: [[166, 152, "P12 points · reversed", 174]],
    labels: [],
  };
})();

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

function signal({ x, y, up, down, ok, label }) {
  // A two-aspect head on a short post; only the live aspect is lit.
  const lamps = (hx, hy) => `<rect class="sig-head" x="${hx}" y="${hy}" width="26" height="54" rx="13"/>`
    + `<circle class="${ok ? "lamp-off" : "lamp-red"}" cx="${hx + 13}" cy="${hy + 14}" r="8"/>`
    + `<circle class="${ok ? "lamp-green" : "lamp-off"}" cx="${hx + 13}" cy="${hy + 40}" r="8"/>`;
  const verdict = `${label} · answer <tspan class="ok">pass</tspan>`;
  const steps = `steps <tspan class="${ok ? "ok" : "bad"}">${ok ? "pass" : "fail"}</tspan>`;
  if (down) {
    // Narrow layout: the head stands at the end of its line, plates to its left.
    return lamps(x, y) + `<text class="sig-text" x="${x - 10}" y="${y + 22}" text-anchor="end">${verdict}</text>`
      + `<text class="sig-text" x="${x - 10}" y="${y + 42}" text-anchor="end">${steps}</text>`;
  }
  const hy = up ? y - 92 : y + 34;
  const post = up ? `<line class="sig-post" x1="${x + 13}" y1="${y - 14}" x2="${x + 13}" y2="${hy + 54}"/>`
                  : `<line class="sig-post" x1="${x + 13}" y1="${y + 14}" x2="${x + 13}" y2="${hy}"/>`;
  const ty = up ? hy + 20 : hy + 22;
  return post + lamps(x, hy) + `<text class="sig-text" x="${x + 40}" y="${ty}">${verdict}</text>`
    + `<text class="sig-text" x="${x + 40}" y="${ty + 20}">${steps}</text>`;
}

function plate(x, y, text, w) {
  return `<g class="t-plate"><rect x="${x}" y="${y}" width="${w}" height="22" rx="2"/><text x="${x + 10}" y="${y + 15}">${esc(text)}</text></g>`;
}

function drawPanel(L, selected) {
  const ids = Object.keys(L.track);
  const base = ids.map((id) => `<path class="t-base" d="${L.track[id]}"/>`).join("")
    + L.onward.map((d) => `<path class="t-base" d="${d}" opacity="0.45"/>`).join("");
  const lit = ids.map((id, n) => `<path class="t-lit" data-routes="${ROUTES[id]}" style="--d:${n * 110}ms" d="${L.track[id]}"/>`).join("");
  const berths = BERTHS.map((b) => {
    const [x, y] = L.berths[b.id];
    const [x1, y1, x2, y2] = L.tick(b, [x, y]);
    return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="var(--panel-line)" stroke-width="1.5"/>`
      + `<g class="berth" data-id="${b.id}" data-routes="${b.routes.join(" ")}" tabindex="0" role="button" aria-pressed="${b.id === selected}" aria-label="${esc(b.head)}">`
      + `<rect x="${x - L.bw / 2}" y="${y}" width="${L.bw}" height="${L.bh}" rx="2"/>`
      + `<text class="b-id" x="${x - L.bw / 2 + 8}" y="${y - 6}">${b.id}</text>`
      + `<text x="${x}" y="${y + L.bh / 2 + L.font * 0.36}" text-anchor="middle" style="font-size:${L.font}px">${esc(b.text)}</text></g>`;
  }).join("");
  const [px, py] = L.points;
  const points = `<path class="points" d="M${px} ${py - 11} L${px + 11} ${py} L${px} ${py + 11} L${px - 11} ${py} Z"/>`;
  const plates = L.plates.map(([x, y, t, w]) => plate(x, y, t, w)).join("");
  const labels = L.labels.map(([x, y, t]) => `<text class="t-label" x="${x}" y="${y}">${esc(t)}</text>`).join("");
  return `<svg viewBox="0 0 ${L.w} ${L.h}" role="presentation" focusable="false">${base}${lit}${points}`
    + L.signals.map(signal).join("") + berths + plates + labels + `</svg>`;
}

function mountPanel() {
  const host = document.getElementById("track");
  if (!host) return;
  const animate = !matchMedia("(prefers-reduced-motion: reduce)").matches;
  const head = document.getElementById("desc-berth");
  const text = document.getElementById("desc-text");
  let layout = null;
  let route = "both";
  let selected = "P12";
  let svg;

  const paint = (setting) => {
    const live = (el) => route === "both" || el.dataset.routes.split(" ").includes(route);
    if (setting && animate) {
      svg.classList.remove("setting");
      void svg.getBoundingClientRect();
      svg.classList.add("setting");
    }
    svg.querySelectorAll(".t-lit").forEach((p) => p.classList.toggle("on", live(p)));
    svg.querySelectorAll(".berth").forEach((g) => g.classList.toggle("dark", !live(g)));
  };

  const render = (first) => {
    const next = host.clientWidth < 640 ? NARROW : WIDE;
    if (next === layout) return;
    layout = next;
    host.innerHTML = drawPanel(layout, selected);
    svg = host.querySelector("svg");
    paint(first);
  };

  const read = (id) => {
    selected = id;
    const b = BERTHS.find((x) => x.id === id);
    svg.querySelectorAll(".berth").forEach((g) => g.setAttribute("aria-pressed", String(g.dataset.id === id)));
    head.textContent = b.head;
    text.textContent = b.read;
  };

  document.querySelectorAll(".nx-btn").forEach((b) => b.addEventListener("click", () => {
    route = b.dataset.route;
    document.querySelectorAll(".nx-btn").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    paint(true);
  }));
  host.addEventListener("click", (e) => { const g = e.target.closest(".berth"); if (g) read(g.dataset.id); });
  host.addEventListener("keydown", (e) => {
    const g = e.target.closest(".berth");
    if (!g) return;
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); read(g.dataset.id); }
    if (["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp"].includes(e.key)) {
      e.preventDefault();
      const all = [...svg.querySelectorAll(".berth")];
      const next = all[(all.indexOf(g) + (e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : -1) + all.length) % all.length];
      next.focus();
      read(next.dataset.id);
    }
  });

  // The route sets itself once on arrival, lamp by lamp; after that the visitor sets it.
  render(true);
  new ResizeObserver(() => render(false)).observe(host);
}

function mountCopy() {
  document.querySelectorAll("[data-copy]").forEach((box) => {
    const button = box.querySelector(".copy");
    const code = box.querySelector("code");
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(button.dataset.text || code.textContent);
        button.textContent = "Copied";
      } catch {
        const range = document.createRange();
        range.selectNodeContents(code);
        getSelection().removeAllRanges();
        getSelection().addRange(range);
        button.textContent = "Selected";
      }
      button.dataset.done = "";
      setTimeout(() => { button.textContent = "Copy"; delete button.dataset.done; }, 1600);
    });
  });
}

mountPanel();
mountCopy();
