// Run: node test_mock.mjs   (loads worker.js through a temp copy with a JSON import attribute)
import fs from "node:fs";
import path from "node:path";
import assert from "node:assert/strict";
import { fileURLToPath, pathToFileURL } from "node:url";

const dir = path.dirname(fileURLToPath(import.meta.url));
const tmp = path.join(dir, ".worker.node.mjs");
fs.writeFileSync(tmp, fs.readFileSync(path.join(dir, "worker.js"), "utf8")
  .replace('"./test-customers.json";', '"./test-customers.json" with { type: "json" };'));
const worker = (await import(pathToFileURL(tmp).href)).default;
fs.unlinkSync(tmp);
const DATA = JSON.parse(fs.readFileSync(path.join(dir, "test-customers.json"), "utf8"));

const BASE = "https://mock.test";
let pass = 0, fail = 0;
async function call(svc, data, raw) {
  const init = { method: "POST", headers: { "content-type": "application/json" }, body: raw !== undefined ? raw : JSON.stringify({ data, call: { ani: "1", lang: "tr" } }) };
  const r = await worker.fetch(new Request(`${BASE}/voice/v1/${svc}`, init));
  const text = await r.text();
  return { status: r.status, text, json: JSON.parse(text), headers: r.headers };
}
function check(name, fn) {
  return Promise.resolve().then(fn).then(() => { pass++; }, (e) => { fail++; console.log("FAIL", name, "\n  ", e.message.split("\n")[0]); });
}

// 1. every stored entry, byte-exact
const jobs = [];
for (const c of DATA.customers) {
  for (const [svc, v] of Object.entries(c.services)) {
    const entries = [];
    if (v.http !== undefined) entries.push([{ customerNo: c.customerNo }, v]);
    else if (svc === "cards.balance" || svc === "statement.week.get") for (const [card, e] of Object.entries(v)) entries.push([{ cardNumber: card }, e]);
    else for (const [card, weeks] of Object.entries(v)) for (const [w, e] of Object.entries(weeks)) entries.push([{ cardNumber: card, newStatementWeek: Number(w) }, e]);
    for (const [d, e] of entries) {
      jobs.push(check(`${c.customerNo} ${svc} ${JSON.stringify(d)}`, async () => {
        const r = await call(svc, d);
        assert.equal(r.status, e.http);
        assert.equal(r.text, JSON.stringify(e.response));
        assert.ok(!("delaySeconds" in r.json));
      }));
    }
  }
}
await Promise.all(jobs);
console.log("stored entries checked:", pass, "failed:", fail);

const code = (r) => r.json.result.code;
await check("number customerNo", async () => { const r = await call("cards.list", { customerNo: 90000001 }); assert.equal(r.status, 200); });
await check("string week", async () => { const r = await call("statement.week.decide", { cardNumber: "540000******1234", newStatementWeek: "2" }); assert.equal(r.status, 200); });
await check("missing body", async () => { const r = await call("cards.list", null, "garbage"); assert.equal(r.status, 400); assert.equal(r.json.result.message, "customer number cannot be empty"); assert.ok(!("data" in r.json)); });
await check("top-level fallback", async () => { const r = await call("cards.list", null, JSON.stringify({ customerNo: "90000001" })); assert.equal(r.status, 200); });
await check("400 card", async () => { const r = await call("cards.balance", { cardNumber: "" }); assert.equal(r.status, 400); assert.equal(r.json.result.message, "card number cannot be empty"); assert.equal(r.json.result.details.rootCause, "InvalidArgumentsException"); assert.equal(r.json.result.details.guid, r.json.result.guid); assert.match(r.json.result.guid, /^[0-9a-f]{32}$/); });
await check("400 week", async () => { const r = await call("statement.week.change", { cardNumber: "540000******1234" }); assert.equal(r.status, 400); assert.equal(r.json.result.message, "new statement week cannot be empty"); });
await check("400 intAddr empty", async () => { const r = await call("contact.email.get", { customerNo: "90000001", intAddressInfoType: "" }); assert.equal(r.status, 400); assert.equal(r.json.result.message, "intAddressInfoType cannot be empty"); });
await check("intAddr absent ok", async () => { const r = await call("contact.email.get", { customerNo: "90000001" }); assert.equal(r.status, 200); });
await check("404 cards.list", async () => { const r = await call("cards.list", { customerNo: "1" }); assert.equal(r.status, 404); assert.equal(code(r), "FB_IVR_CHANNEL_102"); assert.equal(r.json.result.type, "WARNING"); });
await check("404 balance", async () => { const r = await call("cards.balance", { cardNumber: "540000******0000" }); assert.equal(r.status, 404); assert.equal(code(r), "FB_IVR_CHANNEL_120"); });
await check("404 week.get", async () => { const r = await call("statement.week.get", { cardNumber: "999999******0000" }); assert.equal(r.status, 404); assert.equal(code(r), "FB_IVR_CHANNEL_121"); });
await check("404 pdf", async () => { const r = await call("statement.pdf.send", { customerNo: "1" }); assert.equal(r.status, 404); assert.equal(code(r), "FB_IVR_CHANNEL_107"); });
await check("400 decide unknown card", async () => { const r = await call("statement.week.decide", { cardNumber: "540000******0000", newStatementWeek: 2 }); assert.equal(r.status, 400); assert.equal(code(r), "INVALID_ARGUMENTS_ERROR"); });
await check("400 decide current week", async () => { const r = await call("statement.week.decide", { cardNumber: "540000******1234", newStatementWeek: 1 }); assert.equal(r.status, 400); });
await check("400 email unknown", async () => { const r = await call("contact.email.get", { customerNo: "1" }); assert.equal(r.status, 400); });
await check("template customer", async () => { const r = await call("cards.list", { customerNo: "{{customerNumber}}" }); assert.equal(r.status, 200); assert.equal(r.json.data.cards[0].cardNumber, "540000******1234"); });
await check("template card", async () => { const r = await call("cards.balance", { cardNumber: "{{cardNumber}}" }); assert.equal(r.status, 200); const r2 = await call("cards.balance", { cardNumber: "%7B%7BcardNumber%7D%7D" }); assert.equal(r2.status, 200); });
await check("template week", async () => { const r = await call("statement.week.change", { cardNumber: "{{c}}", newStatementWeek: "{{w}}" }); assert.equal(r.status, 200); });
await check("masked 4+4 leniency", async () => { const r = await call("cards.balance", { cardNumber: "5400********1234" }); assert.equal(r.status, 200); assert.equal(r.json.data.cardNumberLast4, "1234"); });
await check("4+4 no match -> 404", async () => { const r = await call("cards.balance", { cardNumber: "5400********9999" }); assert.equal(r.status, 404); });
await check("whitespace", async () => { const r = await call("cards.balance", { cardNumber: " 540000******1234 " }); assert.equal(r.status, 200); });
await check("trailing slash", async () => { const r = await worker.fetch(new Request(`${BASE}/voice/v1/cards.list/`, { method: "POST", body: JSON.stringify({ data: { customerNo: "90000001" } }) })); assert.equal(r.status, 200); });
await check("content-type + cors", async () => { const r = await call("cards.list", { customerNo: "90000001" }); assert.equal(r.headers.get("content-type"), "application/json; charset=utf-8"); assert.equal(r.headers.get("access-control-allow-origin"), "*"); });
await check("health", async () => { const r = await worker.fetch(new Request(`${BASE}/health`)); assert.deepEqual(await r.json(), { status: "ok" }); });
await check("token", async () => { const r = await worker.fetch(new Request(`${BASE}/oauth2/token`, { method: "POST", body: "grant_type=client_credentials" })); assert.deepEqual(await r.json(), { access_token: "test-token", token_type: "Bearer", expires_in: 1800 }); });
await check("options", async () => { const r = await worker.fetch(new Request(`${BASE}/voice/v1/cards.list`, { method: "OPTIONS" })); assert.ok(r.status < 300); assert.equal(r.headers.get("access-control-allow-origin"), "*"); });
await check("unknown path", async () => { const r = await worker.fetch(new Request(`${BASE}/nope`)); assert.equal(r.status, 404); assert.deepEqual(await r.json(), { error: "not found" }); const r2 = await worker.fetch(new Request(`${BASE}/cards.list`, { method: "POST" })); assert.equal(r2.status, 404); });
await check("openapi", async () => {
  const r = await worker.fetch(new Request(`${BASE}/openapi.json`)); const o = await r.json();
  assert.equal(o.openapi, "3.0.3"); assert.equal(o.servers[0].url, `${BASE}/voice/v1`);
  const ids = Object.values(o.paths).map((p) => p.post.operationId).sort();
  assert.deepEqual(ids, ["cards_balance", "cards_list", "contact_email_get", "statement_pdf_send", "statement_week_change", "statement_week_decide", "statement_week_get"]);
  assert.equal(Object.keys(o.paths).length, 7);
  assert.ok(o.paths["/cards.list"].post.requestBody.content["application/json"].schema.properties.data.required.includes("customerNo"));
});

console.log(`PASS ${pass}  FAIL ${fail}`);
process.exit(fail ? 1 : 0);
