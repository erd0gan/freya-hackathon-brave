// Builds the BRAVE bench figure and injects it into docs/brave-algorithm.html (between BENCH markers).
// Run: node docs/figures/brave-bench/build.mjs
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import * as k from "../kit/iso-kit.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const PAGE = join(HERE, "..", "..", "brave-algorithm.html");
const W = 760, H = 300;

const PLATE = { x: -190, y: -52, w: 380, d: 104, r: 8 };
const XS = [-132, -66, 0, 66, 132]; // stations along the rail
const P = k.fitProjection([...k.boxCorners(PLATE, -17, 0), [-190, 0, 92], [190, 0, 92]], W, H, { pad: 10, azimuth: 78 });
const at = (p) => k.iso(p, P);
const S = (paths, st) => k.solidSvg(paths, st);
const L = (d, st) => k.lineSvg(d, st);
const D = (pts, st) => k.dotsSvg(pts, st);
const byX = (a) => [...a];

const base = () => {
  const o = [`<path class="iso-halo" d="${k.haloOf(PLATE, -17, 12, P)}"/>`];
  for (const [x, y] of k.corners(PLATE, 22)) {
    o.push(S(k.cylinder(x, y, 7, -17, 1.6, P, 28), { tone: "lo" }), S(k.cylinder(x, y, 5.4, -15.4, 3.4, P, 28), { tone: "mid" }));
  }
  o.push(S(k.slabOf(PLATE, -12, 12, P, 8, 1.6), { tone: "mid" }));
  o.push(L(k.planOutline(k.insetPlan(PLATE, 5), 0, P), { tone: "lo" }));
  const sc = k.corners(PLATE, 10);
  o.push(L(sc.map(([x, y]) => k.ring(x, y, 2.2, 0, P, 16)).join(""), { tone: "lo" }), D(sc.map(([x, y]) => at([x, y, 0])), { size: 0.5 }));
  // ruler strip on the front edge
  const strip = { x: -170, y: PLATE.y + PLATE.d - 15, w: 340, d: 7, r: 1 };
  o.push(S(k.slabOf(strip, 0, 0.6, P, 3), { tone: "lo", crease: "none" }));
  const t = k.topTicks(strip.x + 4, strip.x + strip.w - 4, 4, 5, strip.y + 0.8, 0.6, [2, 3.6], P);
  o.push(L(t.minor, { tone: "lo" }), L(t.major, { tone: "mid" }));
  // label plate
  const lab = { x: -170, y: PLATE.y + 10, w: 60, d: 14, r: 2 };
  o.push(S(k.slabOf(lab, 0, 0.8, P, 4), { tone: "lo", crease: "none" }), L(k.planOutline(k.insetPlan(lab, 1.6), 0.8, P), { tone: "faint" }));
  for (const [i, len] of [[0, 40], [1, 28], [2, 34]]) o.push(L(k.lineOnTop([lab.x + 6, lab.y + 3.5 + i * 3.2], [lab.x + 6 + len, lab.y + 3.5 + i * 3.2], 0.8, P), { tone: "lo", free: true }));
  return o.join("");
};

const rail = () => {
  const o = [];
  for (const y of [-9, 4]) {
    o.push(S(k.slabOf({ x: -150, y, w: 300, d: 5, r: 1 }, 0, 3, P, 3, 0.4), { tone: "mid" }));
    o.push(D([-142, 142].map((x) => at([x, y + 2.5, 3])), { size: 0.4 }));
  }
  for (const x of [-156, 150]) {
    o.push(S(k.slabOf({ x, y: -12, w: 6, d: 27, r: 1.2 }, 0, 7, P, 3, 0.5), { tone: "mid" }), D([at([x + 3, -8, 7]), at([x + 3, 11, 7])], { size: 0.45 }));
  }
  return o.join("");
};

// station heads (distinct machines): 0 reader (lit), 1 pin gauge, 2 press, 3 gate, 4 rack
const HEAD_Z = 56;
const post = (x, y) => [
  S(k.cylinder(x, y, 5, 0, 4, P, 24), { tone: "mid" }),
  S(k.cylinder(x, y, 3.2, 4, HEAD_Z - 4, P, 24), { tone: "mid", crease: "none" }),
  L(k.sideArc(x, y, 3.2, 24, P, 12) + k.sideArc(x, y, 3.2, 40, P, 12), { tone: "faint" }),
].join("");
const backPosts = () => XS.map((x) => post(x, -26)).join("");
const frontPosts = () => XS.map((x) => post(x, 26)).join("");

const head = (i, x) => {
  const o = [];
  const bar = { x: x - 11, y: -30, w: 22, d: 60, r: 2.5 };
  o.push(S(k.slabOf(bar, HEAD_Z, 7, P, 4, 0.6), { tone: "mid" }));
  o.push(D([at([x - 6, -24, HEAD_Z + 7]), at([x + 6, -24, HEAD_Z + 7]), at([x - 6, 24, HEAD_Z + 7]), at([x + 6, 24, HEAD_Z + 7])], { size: 0.45 }));
  if (i === 0) { // reader lens: stack of discs
    o.push(S(k.cylinder(x, 0, 8, HEAD_Z + 7, 6, P, 28), { tone: "hi", lit: true }), L(k.knurl(x, 0, 8, HEAD_Z + 7, HEAD_Z + 13, 24, P), { tone: "lo" }));
    o.push(S(k.cylinder(x, 0, 4, HEAD_Z + 13, 4, P, 20), { tone: "hi", lit: true }));
  } else if (i === 1) { // pin gauge: row of pins
    for (const py of [-16, -8, 0, 8, 16]) o.push(S(k.cylinder(x, py, 1.8, HEAD_Z - 12, 12, P, 12), { tone: "hi" }));
    o.push(S(k.slabOf({ x: x - 5, y: -20, w: 10, d: 40, r: 1.5 }, HEAD_Z - 14, 3, P, 3), { tone: "mid" }));
  } else if (i === 2) { // press: plunger
    o.push(S(k.cylinder(x, 0, 9, HEAD_Z + 7, 8, P, 28), { tone: "mid" }), L(k.knurl(x, 0, 9, HEAD_Z + 7, HEAD_Z + 15, 28, P), { tone: "lo" }));
    o.push(S(k.cylinder(x, 0, 3, HEAD_Z - 16, 16, P, 16), { tone: "hi" }));
    o.push(S(k.cylinder(x, 0, 7, HEAD_Z - 22, 6, P, 24), { tone: "hi", lit: true }));
  } else if (i === 3) { // gate: two arms
    for (const sy of [-1, 1]) o.push(S(k.slabOf({ x: x - 3, y: sy > 0 ? 8 : -22, w: 6, d: 14, r: 1 }, HEAD_Z - 14, 14, P, 3), { tone: "hi" }));
    o.push(S(k.cylinder(x, 0, 5, HEAD_Z + 7, 5, P, 20), { tone: "mid" }), L(k.ring(x, 0, 2.6, HEAD_Z + 12, P, 12), { tone: "hi" }));
  } else { // outlet rack
    for (const [dx, h] of [[-8, 8], [0, 12], [8, 16]]) o.push(S(k.slabOf({ x: x + dx - 2.4, y: -14, w: 4.8, d: 28, r: 1 }, HEAD_Z + 7, h, P, 3), { tone: "hi" }));
  }
  return `<g data-head="${i}">${o.join("")}</g>`;
};

const lamps = () => XS.map((x, i) => `<g data-lamp="${i}">${S(k.cylinder(x, 41, 3, 0.6, 2.2, P, 16), { tone: "mid" })}</g>`).join("");

const carriage = () => {
  const c = { x: -20, y: -17, w: 40, d: 34, r: 3 };
  const o = [`<path class="iso-halo" d="${k.haloOf(k.insetPlan(c, 3), 3, 0.1, P)}" style="filter:blur(4px)"/>`];
  o.push(S(k.slabOf(c, 3, 5, P, 5, 0.6), { tone: "mid" }));
  o.push(D([-14, -5, 5, 14].map((x) => at([x, c.y + c.d - 2.6, 8])), { size: 0.4, tone: "lo" }));
  // spec card standing in a holder
  const card = { x: -13, y: -12, w: 3, d: 24, r: 0.8 };
  o.push(S(k.slabOf(card, 8, 20, P, 2, 0.4), { tone: "hi", lit: true }));
  for (let r = 0; r < 4; r++) for (let q = 0; q < 6; q++) if ((r * 7 + q * 3) % 4) o.push(D([at([-9.6, -9 + q * 3.6, 12 + r * 3.6])], { size: 0.5, tone: "hi" }));
  o.push(S(k.cylinder(14, 0, 3, 8, 3, P, 16), { tone: "mid" }));
  return o.join("");
};

const svg = k.figureSvg({
  width: W, height: H,
  label: "A bench with a rail and five stations: a reader lens, a pin gauge, a press, a gate and an outlet rack. A carriage carrying a punched spec card slides from station to station, one lamp per station lighting as it is passed.",
  body: [
    base(), rail(), backPosts(), lamps(),
    `<g data-part="carriage" transform="${k.translateAlong(P, [XS[0], 0, 0])}">${carriage()}</g>`,
    XS.map((x, i) => head(i, x)).join(""), frontPosts(),
  ],
});

const STATIONS = [
  { en: ["Reader lens", "The model reads the brief once and punches the spec card. The only lit part: the only place a model is used."], tr: ["Okuyucu lens", "Model brief'i bir kez okuyup spec kartını deler. Işıklı tek parça: modelin kullanıldığı tek yer."] },
  { en: ["Pin gauge", "Code checks every hole against the tool API's enums and the brief's text. A mismatch sends the card back once (repair round)."], tr: ["Pim ölçer", "Kod her deliği tool API'nin enum'larına ve brief metnine karşı kontrol eder. Uyumsuzluk kartı bir kez geri gönderir (onarım turu)."] },
  { en: ["Press", "A fixed template stamps the card into a workflow. No model: the same spec always gives the same workflow."], tr: ["Pres", "Sabit bir şablon kartı workflow'a basar. Model yok: aynı spec her zaman aynı workflow'u verir."] },
  { en: ["Gate", "The validator proves no account data or bank write is reachable before identity is verified. An error stops the run."], tr: ["Kapı", "Doğrulayıcı, kimlik doğrulanmadan hesap verisine ya da banka yazmasına ulaşılamadığını kanıtlar. Hata çalışmayı durdurur."] },
  { en: ["Outlet rack", "Dry run, save and publish on Freya, export, smoke test. The END line says how far the run got."], tr: ["Çıkış rafı", "Freya'da deneme, kaydet ve yayınla, export, smoke test. END satırı çalışmanın nereye vardığını söyler."] },
];

const css = `${k.ISO_CSS}
.bench.iso{--anatomy-card:#2d2d32;--anatomy-paper:#2d2d32}
.bench .iso-figure{margin:0}
.bench .iso-plate{box-shadow:0 0 0 1px #47474f;border-radius:14px}
.bench .iso-svg{max-width:760px}
.bench [data-lamp] .iso-solid>.iso-top{transition:fill .4s}
.bench [data-lamp][data-on] .iso-solid>.iso-top{fill:#f15a2b}
.bench [data-lamp][data-on] .iso-solid>.iso-shade{fill:#a63d1c}
.bench [data-head]{transition:opacity .4s}
.bench[data-active] [data-head]:not([data-on]){opacity:.55}
.bench [data-head][data-on] .iso-solid>.iso-edge{stroke:#f15a2b}
.bench-info{margin-top:14px;display:grid;grid-template-columns:auto 1fr;gap:4px 18px;align-items:baseline}
.bench-info b{font-family:var(--display);color:var(--orange-2);font-size:18px}
.bench-tabs{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px}
.bench-tabs button{font:600 13px var(--display);color:var(--text);background:var(--panel-2);border:1px solid var(--line);border-radius:8px;padding:7px 12px;cursor:pointer}
.bench-tabs button[aria-pressed="true"]{background:var(--orange);color:#1b1b1e;border-color:var(--orange)}
`;

const fig = k.plateHtml({
  fig: "Fig 1", title: "BRAVE bench", hint: "Hover or pick a station", readout: "reader lens",
  keys: [{ mark: "lit", label: "Lit: the model-facing parts" }, { mark: "raised", label: "Five stations on one rail" }],
  caption: "", body: `<div class="iso-stage" tabindex="0" role="group" aria-label="Pick a station">${svg}</div>`,
});

const live = `
(function(){
const P=${JSON.stringify(P)}, XS=${JSON.stringify(XS)}, ST=${JSON.stringify(STATIONS)};
const root=document.querySelector(".bench"), svg=root.querySelector("svg");
const car=svg.querySelector('[data-part="carriage"]'), heads=[...svg.querySelectorAll("[data-head]")], lamps=[...svg.querySelectorAll("[data-lamp]")];
const readout=root.querySelector("[data-readout]"), tabs=root.querySelector(".bench-tabs"), nm=root.querySelector(".bench-name"), tx=root.querySelector(".bench-text");
const still=matchMedia("(prefers-reduced-motion: reduce)");
const lang=()=>document.documentElement.getAttribute("data-lang")||"en";
let x=XS[0], target=XS[0], cur=0, touring=true, clock=0, idle=0, frame=0, last=0, visible=false;
function paint(){
  const L=lang(); nm.textContent=ST[cur][L][0]; tx.textContent=ST[cur][L][1]; readout.textContent=(cur+1)+"/5 · "+ST[cur][L][0].toLowerCase();
  heads.forEach((h,i)=>{ i===cur?h.setAttribute("data-on",""):h.removeAttribute("data-on"); });
  root.setAttribute("data-active",""); [...tabs.children].forEach((b,i)=>b.setAttribute("aria-pressed",String(i===cur)));
}
function pick(i,hold){ cur=i; target=XS[i]; if(hold){touring=false; clearTimeout(idle); idle=setTimeout(()=>{touring=!still.matches; clock=i*2.4; run();},4200);} paint(); run(); }
ST.forEach((s,i)=>{ const b=document.createElement("button"); b.type="button"; b.addEventListener("click",()=>pick(i,true)); b.addEventListener("mouseenter",()=>pick(i,true)); tabs.appendChild(b); });
function labels(){ [...tabs.children].forEach((b,i)=>{ b.textContent=ST[i][lang()][0]; }); paint(); }
new MutationObserver(labels).observe(document.documentElement,{attributes:true,attributeFilter:["data-lang"]});
function tick(now){
  const dt=last?Math.min((now-last)/1000,1/30):1/60; last=now;
  if(touring){ clock+=dt; const i=Math.floor(clock/2.4)%5; if(i!==cur){cur=i;target=XS[i];paint();} }
  x = still.matches ? target : x+(target-x)*(1-Math.exp(-dt/0.35));
  if(Math.abs(target-x)<0.01) x=target;
  car.setAttribute("transform", translateAlong(P,[x,0,0]));
  lamps.forEach((l,i)=>{ XS[i]<=x+1 ? l.setAttribute("data-on","") : l.removeAttribute("data-on"); });
  frame = visible && (touring || x!==target) ? requestAnimationFrame(tick) : 0;
}
function run(){ if(!frame && visible){ last=0; frame=requestAnimationFrame(tick); } }
new IntersectionObserver(e=>{ visible=e[e.length-1].isIntersecting; if(visible) run(); },{rootMargin:"120px 0px"}).observe(root);
if(still.matches) touring=false;
labels(); car.setAttribute("transform", translateAlong(P,[x,0,0]));
})();
`;

const section = `<!--BENCH-->
<style>${css}</style>
<div class="bench iso" data-theme="dark">
${fig}
<div class="bench-tabs" role="group" aria-label="Stations"></div>
<div class="bench-info"><b class="bench-name"></b><span class="bench-text"></span></div>
</div>
<script>${[
  // the kit functions the live code needs, as plain script
  readFileSync(join(HERE, "..", "kit", "iso-kit.mjs"), "utf8").split("export function pageHtml")[0].replace(/^export /gm, "").replace(/<\/(script)/gi, "<\\/$1"),
].join("")}\n${live.replace(/<\/(script)/gi, "<\\/$1")}</script>
<!--/BENCH-->`;

let html = readFileSync(PAGE, "utf8");
if (!html.includes("<!--BENCH-->")) throw new Error("BENCH markers missing in page");
html = html.replace(/<!--BENCH-->[\s\S]*<!--\/BENCH-->/, () => section);
writeFileSync(PAGE, html);
console.log("bench injected,", Math.round(section.length / 1024), "KB");
