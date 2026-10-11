// Cloudflare Worker: mock of Freya Bank's voice statement/debt services.
// Serves exactly the bodies and status codes in test-customers.json.
import DATA from "./test-customers.json";
import { buildOpenApi } from "./openapi.js";

const CORS = { "Access-Control-Allow-Origin": "*" };
const JSON_HEADERS = { ...CORS, "Content-Type": "application/json; charset=utf-8" };
const ORIGIN_TAG = "ivr-gateway-test:ivr-channel-v0";
const NOT_FOUND_EXC = "com.freyabank.ivrgateway.ivrchannel.rest.exception.NotFoundException";

const SERVICES = [
  "cards.list", "cards.balance", "statement.week.get", "statement.week.decide",
  "statement.week.change", "contact.email.get", "statement.pdf.send",
];
const BY_CUSTOMER = new Set(["cards.list", "contact.email.get", "statement.pdf.send"]);
const CARD_KEYED = ["cards.balance", "statement.week.get", "statement.week.decide", "statement.week.change"];

// Indexes built once at module load.
const CUSTOMERS = new Map();   // customerNo -> customer
const CARD_OWNER = new Map();  // card number key -> customer
for (const c of DATA.customers) {
  CUSTOMERS.set(String(c.customerNo), c);
  for (const svc of CARD_KEYED) {
    const m = c.services[svc];
    if (m) for (const card of Object.keys(m)) CARD_OWNER.set(card, c);
  }
  const list = c.services["cards.list"];
  for (const card of (list && list.response && list.response.data && list.response.data.cards) || []) {
    CARD_OWNER.set(card.cardNumber, c);
  }
}

const TEMPLATE_CUSTOMER = "90000001";
const TEMPLATE_CARD = "540000******1234";

function isTemplateVariable(v) {
  return typeof v === "string" && (v.trim().startsWith("{{") || v.trim().startsWith("%7B%7B"));
}

function hex32() {
  const b = new Uint8Array(16);
  crypto.getRandomValues(b);
  return [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
}

function errorBody(code, message, type, rootCause) {
  const guid = hex32();
  return {
    result: {
      guid, returnCode: code, messageText: message, code, message, type,
      details: { guid, rootCause, errorOrigin: ORIGIN_TAG },
    },
  };
}

function send(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: JSON_HEADERS });
}

const bad = (message) => send(errorBody("INVALID_ARGUMENTS_ERROR", message, "ERROR", "InvalidArgumentsException"), 400);
const notFound = (code, message) => send(errorBody(code, message, "WARNING", NOT_FOUND_EXC), 404);

function present(v) {
  return v !== undefined && v !== null && String(v).trim() !== "";
}

// Resolve the card number key against the data (exact, then first4/last4 leniency).
function resolveCard(raw) {
  const s = String(raw).replace(/\s+/g, "");
  if (CARD_OWNER.has(s)) return s;
  const m = /^(\d{4})\d{0,2}\*+(\d{4})$/.exec(s);
  if (!m) return null;
  const hits = [];
  for (const k of CARD_OWNER.keys()) {
    if (k.slice(0, 4) === m[1] && k.slice(-4) === m[2]) hits.push(k);
  }
  return hits.length === 1 ? hits[0] : null;
}

function respond(entry) {
  return send(entry.response, entry.http);
}

async function withDelay(entry) {
  if (entry.delaySeconds) await new Promise((r) => setTimeout(r, entry.delaySeconds * 1000));
  return respond(entry);
}

async function handleService(svc, request) {
  let body = null;
  try { body = await request.json(); } catch { body = null; }
  if (!body || typeof body !== "object" || Array.isArray(body)) body = {};
  const data = body.data && typeof body.data === "object" ? body.data : body;

  // 1. required fields
  if (BY_CUSTOMER.has(svc)) {
    if (!present(data.customerNo)) return bad("customer number cannot be empty");
    if (svc === "contact.email.get" && "intAddressInfoType" in data && !present(data.intAddressInfoType)) {
      return bad("intAddressInfoType cannot be empty");
    }
  } else {
    if (!present(data.cardNumber)) return bad("card number cannot be empty");
    if ((svc === "statement.week.decide" || svc === "statement.week.change") && !present(data.newStatementWeek)) {
      return bad("new statement week cannot be empty");
    }
  }

  // 2. customer-level lookup
  if (BY_CUSTOMER.has(svc)) {
    let no = String(data.customerNo).trim();
    if (isTemplateVariable(no)) no = TEMPLATE_CUSTOMER;
    const cust = CUSTOMERS.get(no);
    const entry = cust && cust.services[svc];
    if (!entry) {
      if (svc === "cards.list") return notFound("FB_IVR_CHANNEL_102", "Müşteriye ait geçerli kart bilgisi bulunamadı.");
      if (svc === "statement.pdf.send") return notFound("FB_IVR_CHANNEL_107", "Pdf dokümanı bulunamadı");
      return bad("customer not found");
    }
    return withDelay(entry);
  }

  // 3. card-level lookup
  const cardRaw = isTemplateVariable(data.cardNumber) ? TEMPLATE_CARD : data.cardNumber;
  const card = resolveCard(cardRaw);
  const byCard = card && CARD_OWNER.get(card).services[svc];
  const cardEntry = byCard && byCard[card];
  if (!cardEntry) {
    if (svc === "cards.balance") return notFound("FB_IVR_CHANNEL_120", "Kart bakiye servisi hatası: kart bulunamadı");
    if (svc === "statement.week.get") return notFound("FB_IVR_CHANNEL_121", "Hesap kesim servisi hatası: kart bulunamadı");
    return bad("card not found");
  }
  if (svc === "cards.balance" || svc === "statement.week.get") return withDelay(cardEntry);

  // decide / change: by week
  let week = String(data.newStatementWeek).trim();
  if (isTemplateVariable(week)) {
    week = cardEntry["2"] ? "2" : Object.keys(cardEntry)[0];
  }
  const entry = cardEntry[week];
  if (!entry) return bad("card not found for requested week");
  return withDelay(entry);
}

export default {
  async fetch(request) {
    const url = new URL(request.url);
    let path = url.pathname;
    if (path.length > 1 && path.endsWith("/")) path = path.slice(0, -1);

    if (request.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: { ...CORS, "Access-Control-Allow-Methods": "GET, POST, OPTIONS", "Access-Control-Allow-Headers": "*", "Access-Control-Max-Age": "86400" },
      });
    }
    if (request.method === "GET" && path === "/health") return send({ status: "ok" });
    if (request.method === "GET" && path === "/openapi.json") return send(buildOpenApi(url.origin));
    if (request.method === "POST" && path === "/oauth2/token") {
      return send({ access_token: "test-token", token_type: "Bearer", expires_in: 1800 });
    }
    if (request.method === "POST" && path.startsWith("/voice/v1/")) {
      const svc = path.slice("/voice/v1/".length);
      if (SERVICES.includes(svc)) return handleService(svc, request);
    }
    return send({ error: "not found" }, 404);
  },
};
