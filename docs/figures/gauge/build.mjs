// Go/no-go gauge: the spec's values are tried in the real enum holes of the tool API.
// Run: node docs/figures/gauge/build.mjs   (injects into docs/brave-algorithm.html between GAUGE markers)
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import * as k from "../kit/iso-kit.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const PAGE = join(HERE, "..", "..", "brave-algorithm.html");
const W = 760, H = 300;

// Real enum of transfer_to_agent.queue (kit/tools/openapi.json); hole order = enum order.
const ENUM = ["restructuring", "general", "support"];
const HX = [-20, 40, 100];
const MAG_X = -150, REPAIR_X = 150;
const PLATE = { x: -190, y: -56, w: 380, d: 112, r: 8 };
const BAR = { x: -50, y: -16, w: 190, d: 32, r: 3 };
const BAR_TOP = 10;
const P = k.fitProjection([...k.boxCorners(PLATE, -17, 0), [-190, 0, 52], [190, 0, 52]], W, H, { pad: 12, azimuth: 76 });
const at = (p) => k.iso(p, P);
const S = (paths, st) => k.solidSvg(paths, st);
const L = (d, st) => k.lineSvg(d, st);
const D = (pts, st) => k.dotsSvg(pts, st);

const base = () => {
  const o = [`<path class="iso-halo" d="${k.haloOf(PLATE, -17, 12, P)}"/>`];
  for (const [x, y] of k.corners(PLATE, 22)) o.push(S(k.cylinder(x, y, 7, -17, 1.6, P, 28), { tone: "lo" }), S(k.cylinder(x, y, 5.4, -15.4, 3.4, P, 28), { tone: "mid" }));
  o.push(S(k.slabOf(PLATE, -12, 12, P, 8, 1.6), { tone: "mid" }), L(k.planOutline(k.insetPlan(PLATE, 5), 0, P), { tone: "lo" }));
  const sc = k.corners(PLATE, 10);
  o.push(L(sc.map(([x, y]) => k.ring(x, y, 2.2, 0, P, 16)).join(""), { tone: "lo" }), D(sc.map(([x, y]) => at([x, y, 0])), { size: 0.5 }));
  const strip = { x: -170, y: PLATE.y + PLATE.d - 15, w: 340, d: 7, r: 1 };
  o.push(S(k.slabOf(strip, 0, 0.6, P, 3), { tone: "lo", crease: "none" }));
  const t = k.topTicks(strip.x + 4, strip.x + strip.w - 4, 4, 5, strip.y + 0.8, 0.6, [2, 3.6], P);
  o.push(L(t.minor, { tone: "lo" }), L(t.major, { tone: "mid" }));
  return o.join("");
};

const rail = () => {
  const o = [];
  for (const y of [-47, -39]) o.push(S(k.slabOf({ x: -170, y, w: 340, d: 4, r: 1 }, 0, 3, P, 3, 0.4), { tone: "mid" }), D([-162, 162].map((x) => at([x, y + 2, 3])), { size: 0.4 }));
  for (const x of [-176, 170]) o.push(S(k.slabOf({ x, y: -50, w: 6, d: 15, r: 1.2 }, 0, 7, P, 3, 0.5), { tone: "mid" }), D([at([x + 3, -46, 7]), at([x + 3, -41, 7])], { size: 0.45 }));
  return o.join("");
};

const stations = () => {
  const o = [];
  const mag = { x: MAG_X - 22, y: -16, w: 44, d: 32, r: 3 };
  o.push(S(k.slabOf(mag, 0, 6, P, 4, 0.5), { tone: "mid" }), L(k.planOutline(k.insetPlan(mag, 3), 6, P, 4), { tone: "faint" }));
  o.push(D([at([mag.x + 4, mag.y + 4, 6]), at([mag.x + mag.w - 4, mag.y + 4, 6]), at([mag.x + 4, mag.y + mag.d - 4, 6]), at([mag.x + mag.w - 4, mag.y + mag.d - 4, 6])], { size: 0.45 }));
  o.push(k.faceTextSvg(k.topMatrix([mag.x + 5, mag.y + mag.d - 6], 6, P, "x"), "SPEC", { size: 3, tone: "mid" }));
  const rp = { x: REPAIR_X - 16, y: -16, w: 32, d: 32, r: 3 };
  o.push(S(k.slabOf(rp, 0, 6, P, 4, 0.5), { tone: "mid" }), L(k.planOutline(k.insetPlan(rp, 3), 6, P, 4), { tone: "faint" }));
  o.push(k.faceTextSvg(k.topMatrix([rp.x + 4, rp.y + rp.d - 6], 6, P, "x"), "REPAIR", { size: 3, tone: "mid" }));
  return o.join("");
};

const gauge = () => {
  const o = [`<path class="iso-halo" d="${k.haloOf(BAR, 0, BAR_TOP, P)}" style="filter:blur(5px)"/>`];
  o.push(S(k.slabOf(BAR, 0, BAR_TOP, P, 5, 0.7), { tone: "hi", crease: "lo" }));
  ENUM.forEach((name, i) => {
    const x = HX[i];
    o.push(L(k.ring(x, -2, 6.4, BAR_TOP, P, 36), { tone: "mid" }), L(k.ring(x, -2, 5.2, BAR_TOP, P, 36), { tone: "faint" }));
    o.push(S(k.cylinder(x, -2, 5.0, BAR_TOP - 0.2, 0.2, P, 28), { tone: "lo", flat: true }));
    o.push(k.faceTextSvg(k.topMatrix([x - 15, 11], BAR_TOP, P, "x"), name, { size: 3.6, tone: "hi" }));
  });
  o.push(D([at([BAR.x + 4, BAR.y + 4, BAR_TOP]), at([BAR.x + BAR.w - 4, BAR.y + 4, BAR_TOP]), at([BAR.x + 4, BAR.y + BAR.d - 4, BAR_TOP]), at([BAR.x + BAR.w - 4, BAR.y + BAR.d - 4, BAR_TOP])], { size: 0.45 }));
  o.push(S(k.slabOf({ x: BAR.x + BAR.w + 4, y: -6, w: 6, d: 12, r: 1.5 }, 0, 5, P, 3), { tone: "mid" }));
  return o.join("");
};

const lamps = () => `<g data-lamp="ok">${S(k.cylinder(BAR.x + BAR.w + 7, 0, 2.6, 5, 2.4, P, 16), { tone: "mid" })}</g><g data-lamp="no">${S(k.cylinder(BAR.x - 10, 0, 2.6, 0, 2.4, P, 16), { tone: "mid" })}</g>`;

const carriage = () => {
  const o = [`<path class="iso-halo" d="${k.haloOf({ x: -16, y: -50, w: 32, d: 14, r: 2 }, 3, 0.1, P)}" style="filter:blur(3px)"/>`];
  o.push(S(k.slabOf({ x: -16, y: -50, w: 32, d: 15, r: 2.5 }, 3, 6, P, 4, 0.5), { tone: "mid" }));
  o.push(D([-10, 10].map((x) => at([x, -46, 9])), { size: 0.4, tone: "lo" }));
  o.push(S(k.slabOf({ x: -6, y: -36, w: 12, d: 38, r: 2 }, 22, 4, P, 3, 0.4), { tone: "mid" }));
  o.push(S(k.slabOf({ x: -5, y: -47, w: 10, d: 12, r: 1.5 }, 9, 13, P, 3), { tone: "mid", crease: "none" }));
  o.push(S(k.cylinder(0, -2, 2.6, 18, 4, P, 16), { tone: "lo" }));
  return o.join("");
};
const peg = () => S(k.cylinder(0, -2, 4.4, 8, 10, P, 28), { tone: "hi", lit: true }) + L(k.sideArc(0, -2, 4.4, 14, P, 14), { tone: "faint" });
const square = () => S(k.slabOf({ x: -6.6, y: -8.6, w: 13.2, d: 13.2, r: 1 }, 8, 10, P, 3, 0.4), { tone: "hi", lit: true });

const svg = k.figureSvg({
  width: W, height: H,
  label: "A go/no-go gauge. A carriage on a rail carries a value from the spec, as a token, over a bar with three round holes, one for each value the tool API allows. A round token drops into its hole; a square token, a value the model invented, does not fit and is sent to the repair post and swapped for a round one.",
  body: [
    base(), rail(), stations(), gauge(), lamps(),
    `<g data-part="carriage" transform="${k.translateAlong(P, [MAG_X, 0, 0])}">${carriage()}<g data-part="token" transform="${k.translateAlong(P, [0, 0, 0])}"><g data-shape="peg">${peg()}</g><g data-shape="square" style="display:none">${square()}</g></g></g>`,
  ],
});

const TEXT = {
  en: {
    title: "The check, as a gauge", hint: "Hover to pause · click to replay",
    steps: {
      load: 'the model wrote "{v}"',
      carry: 'trying "{v}" in the API\'s slots',
      fit: '"{v}" fits → kept',
      jam: '"{v}" fits no slot → sent back',
      repair: "repair round: the model must pick a real value",
      refit: '"{v}" fits → kept',
    },
    cap: "Each round token is a value the model wrote into the spec; each hole is a value the tool API actually allows (the real enum of transfer_to_agent.queue). A value that is not in the enum can never be recorded: it is sent back once for repair. The invented value in this loop is an example; the holes and the final value (the spec sends human requests to general) are real.",
  },
  tr: {
    title: "Kontrol bir mastar olarak", hint: "Durdurmak için üstüne gel · tekrar için tıkla",
    steps: {
      load: 'model "{v}" yazdı',
      carry: '"{v}" API\'nin yuvalarında deneniyor',
      fit: '"{v}" uydu → tutuldu',
      jam: '"{v}" hiçbir yuvaya uymadı → geri gönderildi',
      repair: "onarım turu: model gerçek bir değer seçmeli",
      refit: '"{v}" uydu → tutuldu',
    },
    cap: "Her yuvarlak jeton modelin spec'e yazdığı bir değerdir; her delik tool API'nin gerçekten kabul ettiği bir değerdir (transfer_to_agent.queue'nun gerçek enum'u). Enum'da olmayan bir değer asla kaydedilemez: onarım için bir kez geri gönderilir. Bu döngüdeki uydurulmuş değer örnektir; delikler ve son değer (spec insan isteklerini general'a gönderir) gerçektir.",
  },
};

const css = `${k.ISO_CSS}
.gauge.iso{--anatomy-card:#2d2d32;--anatomy-paper:#2d2d32}
.gauge .iso-figure{margin:0}
.gauge .iso-plate{box-shadow:0 0 0 1px #47474f;border-radius:14px}
.gauge .iso-svg{max-width:760px}
.gauge [data-lamp] .iso-solid>.iso-top{transition:fill .25s}
.gauge [data-lamp="ok"][data-on] .iso-solid>.iso-top{fill:#5cc98b}
.gauge [data-lamp="ok"][data-on] .iso-solid>.iso-shade{fill:#2f7a52}
.gauge [data-lamp="no"][data-on] .iso-solid>.iso-top{fill:#f15a2b}
.gauge [data-lamp="no"][data-on] .iso-solid>.iso-shade{fill:#a63d1c}
.gauge-cap{margin:14px 2px 0;color:var(--muted);font-size:15px;max-width:860px}
.gauge .iso-plate-corner[data-corner="readout"]{max-width:70%;white-space:normal;text-align:right;line-height:1.3}
@media (max-width:560px){.gauge .iso-plate{padding:48px 8px 84px}}
`;

const fig = k.plateHtml({
  fig: "Fig 2", title: "-", hint: "-", readout: "",
  keys: [], caption: "", body: `<div class="iso-stage" role="img" aria-label="Go/no-go gauge: spec values tried in the API's enum holes" style="cursor:pointer">${svg}</div>`,
});

const live = `
(function(){
const P=${JSON.stringify(P)}, HX=${JSON.stringify(HX)}, MAG=${MAG_X}, REP=${REPAIR_X}, T=${JSON.stringify(TEXT)};
const root=document.querySelector(".gauge"), svg=root.querySelector("svg");
const car=svg.querySelector('[data-part="carriage"]'), tok=svg.querySelector('[data-part="token"]');
const pegG=svg.querySelector('[data-shape="peg"]'), sqG=svg.querySelector('[data-shape="square"]');
const lampOk=svg.querySelector('[data-lamp="ok"]'), lampNo=svg.querySelector('[data-lamp="no"]');
const readout=root.querySelector("[data-readout]"), cap=root.querySelector(".gauge-cap"), still=matchMedia("(prefers-reduced-motion: reduce)");
const lang=()=>document.documentElement.getAttribute("data-lang")||"en";
const RUNS=[["restructuring",0],["support",2],["human",-1,"general",1]];
let run=0, paused=false, frame=0, last=0, visible=false;
const ease=u=>u<0?0:u>1?1:u*u*(3-2*u);
function plan(r){
  const [v,h,fix,fh]=r;
  if(h>=0) return [{n:"load",d:.8,v,x0:MAG,x1:MAG,sq:false},{n:"carry",d:1.6,v,x0:MAG,x1:HX[h],sq:false},{n:"fit",d:1.1,v,x0:HX[h],x1:HX[h],sq:false,drop:1,lamp:"ok"}];
  return [{n:"load",d:.8,v,x0:MAG,x1:MAG,sq:true},{n:"carry",d:2.4,v,x0:MAG,x1:HX[2],sq:true},{n:"jam",d:1,v,x0:HX[2],x1:HX[2],sq:true,lamp:"no"},{n:"carry",d:1.8,v,x0:HX[2],x1:REP,sq:true},{n:"repair",d:1.2,v:fix,x0:REP,x1:REP,sq:false,swap:1},{n:"carry",d:1.8,v:fix,x0:REP,x1:HX[fh],sq:false},{n:"refit",d:1.1,v:fix,x0:HX[fh],x1:HX[fh],sq:false,drop:1,lamp:"ok"}];
}
let ph=plan(RUNS[0]), pi=0, pt=0;
function say(p){ readout.textContent=T[lang()].steps[p.n].replace("{v}",p.v); }
function apply(p,u){
  const x=still.matches?p.x1:p.x0+(p.x1-p.x0)*ease(u);
  let dz=p.drop?-6*ease(u*3):0;
  if(p.n==="jam") dz=-1.5*Math.sin(Math.min(1,u*3)*Math.PI);
  const sq=p.swap&&u>.5?false:p.sq; pegG.style.display=sq?"none":""; sqG.style.display=sq?"":"none";
  car.setAttribute("transform",translateAlong(P,[x,0,0])); tok.setAttribute("transform",translateAlong(P,[0,0,dz]));
  const w=p.lamp&&u>.15?p.lamp:null; lampOk.toggleAttribute("data-on",w==="ok"); lampNo.toggleAttribute("data-on",w==="no");
}
function tick(now){
  const dt=last?Math.min((now-last)/1000,1/30):1/60; last=now;
  if(!paused){ pt+=dt; let p=ph[pi]; while(pt>=p.d){ pt-=p.d; pi++; if(pi>=ph.length){ pi=0; run=(run+1)%RUNS.length; ph=plan(RUNS[run]); } p=ph[pi]; } apply(p,pt/p.d); say(p); }
  frame=visible?requestAnimationFrame(tick):0;
}
function go(){ if(!frame&&visible){ last=0; frame=requestAnimationFrame(tick); } }
function tr(){ const L=T[lang()]; root.querySelector('[data-corner="title"]').textContent=L.title; root.querySelector('[data-corner="hint"]').textContent=L.hint; cap.textContent=L.cap; say(ph[pi]); }
const stage=root.querySelector(".iso-stage");
stage.addEventListener("pointerenter",()=>{paused=true;}); stage.addEventListener("pointerleave",()=>{paused=false;});
stage.addEventListener("click",()=>{ run=0; ph=plan(RUNS[0]); pi=0; pt=0; paused=false; go(); });
new IntersectionObserver(e=>{ visible=e[e.length-1].isIntersecting; if(visible) go(); },{rootMargin:"120px 0px"}).observe(root);
new MutationObserver(tr).observe(document.documentElement,{attributes:true,attributeFilter:["data-lang"]});
apply(ph[0],0); tr();
})();
`;

const kitSrc = readFileSync(join(HERE, "..", "kit", "iso-kit.mjs"), "utf8").split("export function pageHtml")[0].replace(/^export /gm, "");
const section = `<!--GAUGE-->
<style>${css}</style>
<div class="gauge iso" data-theme="dark">
${fig}
<p class="gauge-cap"></p>
</div>
<script>${kitSrc}\n${live}</script>
<!--/GAUGE-->`;

let html = readFileSync(PAGE, "utf8");
if (!html.includes("<!--GAUGE-->")) throw new Error("GAUGE markers missing in page");
html = html.replace(/<!--GAUGE-->[\s\S]*<!--\/GAUGE-->/, () => section);
writeFileSync(PAGE, html);
console.log("gauge injected,", Math.round(section.length / 1024), "KB");
