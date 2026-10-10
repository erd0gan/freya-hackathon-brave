# Runs INSIDE Freya code nodes and in local tests, after collections_lib and speech_lib.
# Stdlib only, never raises to the caller, never two braces in a row.
#
# The negotiation as a state machine. The model only classifies the customer's reply
# (reply_kind) and copies the words of a date or an amount; this code decides what happens
# and writes the one instruction the model follows next (`say`). Every value that reaches the
# bank (product, amount, date, note reason, queue, outcome) is chosen here.
#
# NEG config (from the spec):
#   attempt_plan: [{"amount": "current"|"full"|"floor", "date": "attempt1"|"deadline"}, ...]
#   reasons: semantic -> bank reason name (refused, hardship, dispute, service_error, paid)
#   outcomes: semantic -> bank outcome name (promise, paid, transferred, no_promise)
#   queues: semantic (restructuring, human, support) -> queue name
#   transfer_reasons: semantic -> close reason
#   notes: true when products without a promise get add_note (Brief B)
#   lines: exact sentences (today_question, recorded, paid, sympathy, ...)
#   multi_today_question

SEMANTIC_KINDS = [
    "yes",
    "no",
    "offer",
    "too_much",
    "vague",
    "paid",
    "dispute",
    "hardship",
    "restructure",
    "human",
    "stop",
    "question",
    "unclear",
]


def _m(x):
    return money(x)


def _d(iso):
    return say_date(datetime.date.fromisoformat(iso)) if iso else ""


def _line(neg, key, default=""):
    return str((neg.get("lines") or {}).get(key) or default)


def new_state(pf, neg, now):
    """pf: product_facts output (pK_* keys)."""
    n = int(pf.get("product_count") or 0)
    ps = []
    for k in range(1, n + 1):
        key = "p" + str(k)
        try:
            full = float(pf.get(key + "_past_due") or 0)
        except Exception:
            full = 0.0
        try:
            floor = float(pf.get(key + "_floor") or 0) or full
        except Exception:
            floor = full
        try:
            att = int(pf.get(key + "_attempts") or 1)
        except Exception:
            att = 1
        ps.append(
            {
                "id": pf.get(key + "_id") or key,
                "name": pf.get(key + "_name") or "",
                "full": full,
                "floor": floor,
                "deadline": pf.get(key + "_deadline") or "",
                "days_late": pf.get(key + "_days_late") or "",
                "attempts": att,
                "used": 0,
                "too_much": False,
                "floor_offered": False,
                "vague_asked": False,
                "status": "open",
                "amount": None,
                "date": None,
                "offer": None,
                "own_deadline": False,
            }
        )
    st = {
        "phase": "ask_today",
        "i": 0,
        "p": ps,
        "human_asked": False,
        "stop": 0,
        "transfer": "",
        "action": "say",
        "say": "",
        "rec": [],
        "rec_try": 0,
        "notes": [],
        "final_lines": [],
        "last_ask": "",
    }
    st["say"] = present_text(st, neg)
    st["last_ask"] = st["say"]
    return st


def present_text(st, neg):
    lines = []
    for p in st["p"]:
        lines.append(
            p["name"] + ": " + _m(p["full"]) + " past due, " + str(_days(p)) + " days late."
        )
    multi = len(st["p"]) > 1
    q = (
        _line(neg, "multi_today_question", "Can you pay all of them today?")
        if multi
        else _line(neg, "today_question", "Can you make this payment today?")
    )
    return (
        "Say that you are calling from the collections team about a late payment. "
        + ("State every late product, one sentence each: " if multi else "State: ")
        + " ".join(lines)
        + ' Then ask exactly: "'
        + q
        + '"'
    )


def _days(p):
    return p.get("days_late", "") if p.get("days_late") not in (None, "") else ""


def _current_amount(p):
    return p["floor"] if p["too_much"] else p["full"]


def _attempt_offer(st, k, neg, cal):
    p = st["p"][k]
    plan = neg.get("attempt_plan") or [
        {"amount": "current", "date": "attempt1"},
        {"amount": "floor", "date": "deadline"},
    ]
    step = plan[min(p["used"], len(plan)) - 1]
    amount = (
        p["floor"]
        if step.get("amount") == "floor"
        else (p["full"] if step.get("amount") == "full" else _current_amount(p))
    )
    date = p["deadline"] if step.get("date") == "deadline" else cal.get("attempt1_date", "")
    if date and p["deadline"] and date > p["deadline"]:
        date = p["deadline"]
    p["offer"] = {"amount": amount, "date": date, "kind": "attempt"}
    floor_note = ""
    if amount < p["full"] - 0.005:
        floor_note = " Say that the rest stays overdue."
    return (
        "Propose that the customer pays "
        + _m(amount)
        + " for the "
        + p["name"]
        + " on "
        + _d(date)
        + "."
        + floor_note
        + " Ask if they can do that."
    )


def _flat_refusal(st, k, neg, cal):
    p = st["p"][k]
    if p["used"] < p["attempts"]:
        p["used"] += 1
        return _attempt_offer(st, k, neg, cal)
    p["status"] = "refused"
    p["offer"] = None
    return None


def _agree(p, amount, date, own_deadline=False):
    p["status"] = "agreed"
    p["amount"] = round(float(amount), 2)
    p["date"] = date
    p["offer"] = None
    p["own_deadline"] = own_deadline


def _apply_offer(st, k, amount, date_iso, date_kind, neg, cal, now, cfg, all_products):
    """The customer named an amount and/or a date for product k (or for all)."""
    p = st["p"][k]
    targets = (
        [j for j, q in enumerate(st["p"]) if q["status"] in ("open", "agreed")]
        if all_products
        else [k]
    )
    if amount is None:
        amount = p["offer"]["amount"] if p.get("offer") else _current_amount(p)
    if amount > p["full"] + 0.005:
        amount = p["full"]
    if amount < p["floor"] - 0.005 and len(targets) == 1:
        if p["floor_offered"]:
            return _flat_refusal(st, k, neg, cal)
        p["floor_offered"] = True
        d = date_iso or (p["offer"] or {}).get("date") or cal.get("today_iso")
        p["offer"] = {"amount": p["floor"], "date": d, "kind": "floor"}
        return (
            "Say that "
            + _m(amount)
            + " cannot be recorded. Offer the minimum of "
            + _m(p["floor"])
            + " on "
            + _d(d)
            + ". Say that the rest stays overdue. Ask if they can do that."
        )
    if not date_iso:
        if date_kind == "vague":
            if not p["vague_asked"]:
                p["vague_asked"] = True
                p["pending_amount"] = amount
                return "Ask once for the exact day on which they will pay."
            p["offer"] = {"amount": amount, "date": p["deadline"], "kind": "deadline"}
            return (
                "Propose "
                + _m(amount)
                + " on "
                + _d(p["deadline"])
                + ", the latest date that can be recorded. Ask if they can do that."
            )
        if p.get("offer") and p["offer"].get("date"):
            date_iso = p["offer"]["date"]
        else:
            p["pending_amount"] = amount
            return "Ask on which exact day they can pay " + _m(amount) + "."
    said = []
    for j in targets:
        q = st["p"][j]
        amt = amount if j == k else (q["amount"] if q["status"] == "agreed" else _current_amount(q))
        res = check_promise_date(date_iso, q["deadline"], now, cfg)
        if res == "ok":
            _agree(q, amt, date_iso)
        elif res.startswith("after_deadline") and len(targets) > 1:
            _agree(q, amt, q["deadline"], own_deadline=True)
        elif res.startswith("after_deadline"):
            q["offer"] = {"amount": amt, "date": q["deadline"], "kind": "deadline"}
            said.append(
                "Say that the latest date you can record is "
                + _d(q["deadline"])
                + " and offer "
                + _m(amt)
                + " on that day. Ask if they can do that."
            )
        elif res.startswith("not_business_day"):
            nb = res.split(":", 1)[1]
            q["offer"] = {"amount": amt, "date": nb, "kind": "next_business_day"}
            said.append(
                "Say that "
                + _d(date_iso)
                + " is not a business day and offer the next business day, "
                + _d(nb)
                + ", for "
                + _m(amt)
                + ". Ask if they can do that."
            )
        elif res == "past":
            said.append("Say the date must be today or later and ask for another day.")
        else:
            said.append("Ask for the exact day again.")
        if said:
            st["i"] = j
            return said[0]
    return None


def _next_open(st):
    for j, q in enumerate(st["p"]):
        if q["status"] == "open":
            return j
    return -1


def _readback(st, neg):
    parts = []
    for q in st["p"]:
        if q["status"] == "agreed":
            parts.append(
                q["name"]
                + ": "
                + _m(q["amount"])
                + " on "
                + _d(q["date"])
                + (
                    " (its latest possible date, so it cannot be later)"
                    if q.get("own_deadline")
                    else ""
                )
            )
    return (
        "Read back every promise: "
        + "; ".join(parts)
        + '. Then ask exactly: "'
        + _line(neg, "confirm_question", "Do you confirm?")
        + '"'
    )


def _advance(st, neg, cal):
    """Move to the next open product, to the read-back, or to the end."""
    j = _next_open(st)
    if j >= 0:
        ack = ""
        just = [q for q in st["p"] if q["status"] == "agreed" and not q.get("acked")]
        if just:
            ack = (
                "Say briefly that you have noted "
                + ", ".join(
                    q["name"] + " for " + _m(q["amount"]) + " on " + _d(q["date"]) for q in just
                )
                + ". Then: "
            )
            for q in just:
                q["acked"] = True
        st["i"] = j
        st["phase"] = "offer"
        p = st["p"][j]
        if p["used"] == 0:
            p["used"] = 1
        return ack + _attempt_offer(st, j, neg, cal)
    if any(q["status"] == "agreed" for q in st["p"]):
        st["phase"] = "confirm"
        return _readback(st, neg)
    return None


def _finish(st, neg):
    st["action"] = "finish"
    oc = neg.get("outcomes") or {}
    rs = neg.get("reasons") or {}
    if st["transfer"]:
        st["final_outcome"] = oc.get("transferred", "transferred")
        st["final_reason"] = (neg.get("transfer_reasons") or {}).get(st["transfer"], st["transfer"])
        st["notes"] = []
        return
    sem = {
        "refused": "refused",
        "open": "refused",
        "hardship": "hardship",
        "dispute": "dispute",
        "failed": "service_error",
        "paid": "paid",
    }
    notes = []
    for q in st["p"]:
        if q["status"] != "recorded":
            notes.append(
                [q["id"], rs.get(sem.get(q["status"], "refused"), sem.get(q["status"], "refused"))]
            )
    st["notes"] = notes if neg.get("notes") else []
    if any(q["status"] == "recorded" for q in st["p"]):
        st["final_outcome"] = oc.get("promise", "promise")
        st["final_reason"] = ""
        return
    first = next((q for q in st["p"] if q["status"] != "paid"), None)
    if first is None:
        st["final_outcome"] = oc.get("paid", "paid")
        st["final_reason"] = ""
        return
    st["final_outcome"] = oc.get("no_promise", "no_promise")
    s = sem.get(first["status"], "refused")
    st["final_reason"] = rs.get(s, s)


def _transfer(st, what, neg):
    st["transfer"] = what
    st["transfer_queue"] = (neg.get("queues") or {}).get(what, what)
    # Brief B rule 10: agreed but unrecorded promises are dropped.
    for q in st["p"]:
        if q["status"] == "agreed":
            q["status"] = "open"
    st["final_lines"].append(
        _line(neg, "transfer_" + what, "I am connecting you with a colleague now.")
    )
    _finish(st, neg)
    st["action"] = "transfer"


def step(st, r, neg, cal, now, cfg):
    """r: {kind, amount, date, date_kind, all, product}. Mutates and returns st."""
    st["action"] = "say"
    kind = r.get("kind") or "unclear"
    k = st["i"]
    if r.get("product") is not None and 0 <= r["product"] < len(st["p"]):
        k = r["product"]
    say = None
    phase = st["phase"]
    if kind == "restructure":
        _transfer(st, "restructuring", neg)
        return st
    if kind == "human":
        if not st["human_asked"]:
            st["human_asked"] = True
            st["say"] = "Ask once whether you can help them yourself first. Then: " + st["last_ask"]
            return st
        _transfer(st, "human", neg)
        return st
    if phase == "hardship_offer":
        if kind == "yes":
            _transfer(st, "support", neg)
            return st
        if kind != "offer":
            for q in st["p"]:
                if q["status"] in ("open", "agreed"):
                    q["status"] = "hardship"
            say = _advance(st, neg, cal)
            if say is None:
                _finish(st, neg)
                return st
            st["say"] = say
            return st
        phase = "offer"
    elif kind == "hardship":
        st["phase"] = "hardship_offer"
        st["say"] = (
            "Say one short sentence of sympathy. Make no payment request. Offer once to "
            "connect them with the customer support team and ask if they want that."
        )
        return st
    if kind == "stop":
        st["stop"] += 1
        if st["stop"] >= 2:
            for q in st["p"]:
                if q["status"] in ("open", "agreed"):
                    q["status"] = "refused"
            _finish(st, neg)
            return st
        st["say"] = "Acknowledge briefly. Then: " + st["last_ask"]
        return st
    if kind in ("paid", "dispute"):
        p = st["p"][k]
        p["status"] = kind
        p["offer"] = None
        if kind == "paid":
            st["final_lines"].append(
                _line(neg, "paid", "Payments can take one business day to appear.")
            )
        say = _advance(st, neg, cal) if len(st["p"]) > 1 else None
        if say is None:
            _finish(st, neg)
            return st
        pre = (
            "Say that payments can take one business day to appear. "
            if kind == "paid"
            else "Do not argue; say you have noted it. "
        )
        st["say"] = pre + "Then continue: " + say
        st["last_ask"] = say
        return st
    if kind == "question":
        st["say"] = (
            "Answer the customer's question briefly with what you know from this call; "
            "if you do not have the information, say so. Then repeat: " + st["last_ask"]
        )
        return st
    if phase == "closing":
        _finish(st, neg)
        return st
    if phase == "confirm":
        if kind == "yes":
            st["rec"] = [j for j, q in enumerate(st["p"]) if q["status"] == "agreed"]
            st["rec_try"] = 0
            st["action"] = "record"
            st["phase"] = "recording"
            _set_rec(st)
            return st
        if kind == "offer":
            agreed = [j for j, q in enumerate(st["p"]) if q["status"] == "agreed"]
            j = k if k in agreed else (agreed[0] if agreed else k)
            say = _apply_offer(
                st,
                j,
                r.get("amount"),
                r.get("date"),
                r.get("date_kind"),
                neg,
                cal,
                now,
                cfg,
                bool(r.get("all")) and len(agreed) > 1,
            )
            st["say"] = say or _readback(st, neg)
            if say is None:
                st["phase"] = "confirm"
            st["last_ask"] = st["say"]
            return st
        if kind == "no":
            agreed = [j for j, q in enumerate(st["p"]) if q["status"] == "agreed"]
            if len(agreed) == 1:
                j = agreed[0]
                st["p"][j]["status"] = "open"
                say = _flat_refusal(st, j, neg, cal)
                if say is None:
                    say = _advance(st, neg, cal)
                if say is None:
                    _finish(st, neg)
                    return st
                st["phase"] = "offer" if st["p"][j]["status"] == "open" else st["phase"]
                st["say"] = say
                st["last_ask"] = say
                return st
            st["say"] = "Ask which promise they want to change."
            return st
        st["say"] = "Repeat: " + _readback(st, neg)
        return st
    # ask_today or offer
    p = st["p"][k]
    multi = len(st["p"]) > 1
    if kind == "yes":
        if phase == "ask_today":
            for q in st["p"]:
                if q["status"] == "open":
                    res = check_promise_date(cal.get("today_iso"), q["deadline"], now, cfg)
                    d = (
                        cal.get("today_iso")
                        if res == "ok"
                        else (res.split(":", 1)[1] if ":" in res else q["deadline"])
                    )
                    _agree(q, q["full"], d)
        elif p.get("offer"):
            o = p["offer"]
            _agree(p, o["amount"], o["date"])
        else:
            st["say"] = "Repeat: " + st["last_ask"]
            return st
        say = _advance(st, neg, cal)
    elif kind == "no":
        say = _flat_refusal(st, k, neg, cal)
        if say is None:
            say = _advance(st, neg, cal)
        else:
            st["phase"] = "offer"
    elif kind == "offer" or (kind == "vague"):
        amount = r.get("amount")
        say = _apply_offer(
            st,
            k,
            amount,
            r.get("date"),
            "vague" if kind == "vague" else r.get("date_kind"),
            neg,
            cal,
            now,
            cfg,
            multi
            and amount is None
            and (bool(r.get("all")) or (phase == "ask_today" and r.get("product") is None)),
        )
        if say is None:
            say = _advance(st, neg, cal)
        else:
            st["phase"] = "offer"
    elif kind == "too_much":
        if not p["too_much"]:
            p["too_much"] = True
        if not p["floor_offered"] and p["floor"] < p["full"] - 0.005:
            p["floor_offered"] = True
            d = (p.get("offer") or {}).get("date") or cal.get("today_iso")
            p["offer"] = {"amount": p["floor"], "date": d, "kind": "floor"}
            say = (
                "Offer the minimum of "
                + _m(p["floor"])
                + " for the "
                + p["name"]
                + " on "
                + _d(d)
                + ". Say that the rest stays overdue. Ask if they can do that."
            )
            st["phase"] = "offer"
        else:
            say = _flat_refusal(st, k, neg, cal)
            if say is None:
                say = _advance(st, neg, cal)
            else:
                st["phase"] = "offer"
    else:
        st["say"] = "Repeat: " + st["last_ask"]
        return st
    if say is None:
        _finish(st, neg)
        return st
    st["say"] = say
    st["last_ask"] = say
    return st


def _set_rec(st):
    if st["rec"]:
        q = st["p"][st["rec"][0]]
        st["rec_product_id"] = q["id"]
        st["rec_amount"] = q["amount"]
        st["rec_date"] = q["date"]


def record_result(st, ok, neg, cal):
    """After one create_promise call. Retries once, then marks the product failed."""
    j = st["rec"][0]
    q = st["p"][j]
    if ok:
        q["status"] = "recorded"
        st["rec"].pop(0)
        st["rec_try"] = 0
    elif st["rec_try"] == 0:
        st["rec_try"] = 1
    else:
        q["status"] = "failed"
        st["rec"].pop(0)
        st["rec_try"] = 0
    if st["rec"]:
        st["action"] = "record"
        _set_rec(st)
        return st
    rec = [q for q in st["p"] if q["status"] == "recorded"]
    bad = [q for q in st["p"] if q["status"] == "failed"]
    parts = []
    if rec:
        parts.append(
            "Say that the promise is recorded for "
            + ", ".join(
                q["name"] + " (" + _m(q["amount"]) + " on " + _d(q["date"]) + ")" for q in rec
            )
            + '. Then say exactly: "'
            + _line(neg, "recorded", "You will receive a text message confirming this promise.")
            + '"'
        )
    if bad:
        parts.append(
            "Say that you could not record the promise for "
            + ", ".join(q["name"] for q in bad)
            + "."
        )
    parts.append("Ask if there is anything else.")
    st["action"] = "say"
    st["phase"] = "closing"
    st["say"] = " ".join(parts)
    st["last_ask"] = "Ask if there is anything else."
    return st


def final_say(st, neg):
    lines = list(st.get("final_lines") or [])
    lines.append(_line(neg, "goodbye", "Thank you for your time. Goodbye."))
    return " ".join(x for x in lines if x)


# ---------------------------------------------------------------- glue for code nodes
REPLY_VARS = (
    "reply_kind",
    "said_date_text",
    "said_amount_text",
    "said_amount",
    "all_products",
    "about_product",
)


def read_reply(v, today):
    kind = str(v.get("reply_kind") or "").strip().lower()
    if kind not in SEMANTIC_KINDS:
        kind = "unclear"
    iso, dk = parse_spoken_date(v.get("said_date_text"), today)
    amount = parse_amount(v.get("said_amount_text"))
    if amount is None:
        try:
            amount = (
                float(v.get("said_amount")) if str(v.get("said_amount") or "").strip() else None
            )
        except Exception:
            amount = None
    if kind in ("yes", "no", "too_much") and (iso or amount is not None):
        kind = "offer"  # a date or an amount the customer names is never a refusal
    if kind == "offer" and not iso and amount is None and dk == "vague":
        kind = "vague"
    prod = str(v.get("about_product") or "").strip().lower()
    pi = int(prod[1:]) - 1 if re.fullmatch(r"p[1-9]", prod) else None
    allp = str(v.get("all_products") or "").strip().lower() in ("yes", "true", "all")
    return {
        "kind": kind,
        "amount": amount,
        "date": iso,
        "date_kind": dk,
        "all": allp,
        "product": pi,
    }


def clear_reply(v):
    for k in REPLY_VARS:
        v[k] = ""


def publish(v, st, neg):
    v["neg_state"] = json.dumps(st)
    v["neg_action"] = st.get("action") or "say"
    v["neg_say"] = st.get("say") or ""
    v["neg_phase"] = st.get("phase") or ""
    if st.get("rec"):
        v["rec_product_id"] = str(st.get("rec_product_id") or "")
        v["rec_amount"] = st.get("rec_amount")
        v["rec_date"] = str(st.get("rec_date") or "")
    v["transfer_queue"] = str(st.get("transfer_queue") or "")
    if st.get("action") in ("finish", "transfer"):
        v["final_outcome"] = str(st.get("final_outcome") or "")
        v["final_reason"] = str(st.get("final_reason") or "")
        v["final_say"] = final_say(st, neg)
        notes = st.get("notes") or []
        v["note_count"] = str(len(notes))
        if notes:
            v["note_product_id"] = notes[0][0]
            v["note_reason"] = notes[0][1]


def load_state(v):
    try:
        st = json.loads(v.get("neg_state") or "")
        return st if isinstance(st, dict) else None
    except Exception:
        return None


def pop_note(v):
    """After one add_note call: move to the next note or report none left."""
    st = load_state(v) or {}
    notes = st.get("notes") or []
    if notes:
        notes.pop(0)
    st["notes"] = notes
    v["neg_state"] = json.dumps(st)
    v["note_count"] = str(len(notes))
    if notes:
        v["note_product_id"] = notes[0][0]
        v["note_reason"] = notes[0][1]
