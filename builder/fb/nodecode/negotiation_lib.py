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
#   policy knobs (POLICY_KEYS below): counts and choices a brief states. The builder must set
#   every one of them from the spec; POLICY_DEFAULTS only keeps a hand-run engine working and
#   `missing_policy` lets the builder refuse a config that would fall back to them.

# Brief A/B values. Never relied on by a generated workflow (see missing_policy).
POLICY_DEFAULTS = {
    # A42: ask once whether you can help, transfer on the second request. 1 = transfer at once.
    "human_requests_to_transfer": 2,
    # A45: close after the customer says twice that they do not want to talk.
    "stop_requests_to_close": 2,
    # A37: create_promise calls per product before giving up (first try + one retry).
    "promise_attempts": 2,
    # A37/B11 after the last failed call: "continue" (mark failed, close/note service_error)
    # or "transfer" (transfer to queue `record_failure_queue`).
    "record_failure": "continue",
    "record_failure_queue": "support",
    # A32: "offer_support" (offer a transfer; yes -> transfer, no -> hardship),
    # "transfer" (transfer at once) or "close" (no persuasion, close as hardship).
    "hardship": "offer_support",
    "hardship_queue": "support",
    # B closing: no promise recorded -> reason of the "first_product" (get_account order)
    # whose status is not paid, or the first status of `reason_priority` that any product has.
    "no_promise_reason": "first_product",
    "reason_priority": ["dispute", "hardship", "service_error", "refused"],
    # B10: add_note calls on a transferred call.
    "notes_on_transfer": False,
    # A21: times to ask for the exact day after a vague period, before proposing the deadline.
    "vague_date_asks": 1,
    # A26/A27: times the floor is offered after "too much" or an amount below the floor.
    "floor_offers": 1,
}
POLICY_KEYS = sorted(POLICY_DEFAULTS)


def _pol(neg, key):
    val = neg.get(key)
    return POLICY_DEFAULTS[key] if val is None else val


def missing_policy(neg):
    """Policy keys the config leaves to the defaults."""
    return [k for k in POLICY_KEYS if neg.get(k) is None]


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
    "evasive",
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
                "floor_offered": 0,
                "vague_asked": 0,
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
        "human_asked": 0,
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
    """The exact words that state every late product and ask the first question."""
    lines = []
    for p in st["p"]:
        lines.append(
            "Your "
            + p["name"]
            + " has "
            + _m(p["full"])
            + " past due and is "
            + str(_days(p))
            + " days late."
        )
    multi = len(st["p"]) > 1
    q = (
        _line(neg, "multi_today_question", "Can you pay all of them today?")
        if multi
        else _line(neg, "today_question", "Can you make this payment today?")
    )
    return " ".join(
        x
        for x in [_line(neg, "present_intro", "I am calling about a late payment.")] + lines + [q]
        if x
    )


def _the(name):
    """'the personal loan', but 'your overdraft' stays as it is."""
    n = str(name or "")
    low = n.lower()
    if low.startswith(("the ", "your ", "my ", "a ", "an ")):
        return n
    return "the " + n


# A turn we could not use. Talkative customers' turns often arrive cut off, many in a row; the
# same sentence said again and again annoyed the customer into hanging up, so it rotates.
SORRY = ["Sorry, I did not catch that.", "Sorry, I missed the end of that.", "Take your time."]
CUT_SORRY = "Sorry, I think you were cut off."


def _sorry(st, r):
    n = max(int(st.get("unclear_run") or 1) - 1, 0)
    if r.get("cut") and n % 2 == 1:
        return CUT_SORRY
    return SORRY[n % len(SORRY)]


def _short(st, neg):
    """A short re-ask without amounts or dates: repeating a whole offer reads as making it a
    second time (judge: 'minimum offered twice')."""
    phase = st.get("phase")
    if phase == "confirm":
        return _line(neg, "confirm_question", "Do you confirm?")
    if phase == "closing":
        return _line(neg, "anything_else", "Is there anything else I can help you with?")
    if phase == "hardship_offer":
        return _line(
            neg, "support_offer", "Would you like me to connect you with our customer support team?"
        )
    if phase == "offer":
        return "Can you do that?"
    if len(st.get("p") or []) > 1:
        return _line(neg, "multi_today_question", "Can you pay all of them today?")
    return _line(neg, "today_question", "Can you make this payment today?")


def _rest(neg):
    return _line(neg, "rest_overdue", "The rest stays overdue.")


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
        floor_note = " " + _rest(neg)
    return (
        "Could you pay "
        + _m(amount)
        + " for "
        + _the(p["name"])
        + " on "
        + _d(date)
        + "?"
        + floor_note
    )


def _flat_refusal(st, k, neg, cal):
    p = st["p"][k]
    if st.get("hardship"):
        # A32: after hardship there is no persuasion attempt; the product ends as hardship
        p["status"] = "hardship"
        p["offer"] = None
        return None
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
        if p["floor_offered"] >= _pol(neg, "floor_offers"):
            return _flat_refusal(st, k, neg, cal)
        p["floor_offered"] += 1
        d = date_iso or (p["offer"] or {}).get("date") or cal.get("today_iso")
        p["offer"] = {"amount": p["floor"], "date": d, "kind": "floor"}
        return (
            "I cannot record "
            + _m(amount)
            + ". The minimum is "
            + _m(p["floor"])
            + ". Could you pay "
            + _m(p["floor"])
            + " on "
            + _d(d)
            + "? "
            + _rest(neg)
        )
    if not date_iso:
        if date_kind == "vague":
            if p["vague_asked"] < _pol(neg, "vague_date_asks"):
                p["vague_asked"] += 1
                p["pending_amount"] = amount
                return _line(neg, "vague_ask", "On which exact day will you pay?")
            p["offer"] = {"amount": amount, "date": p["deadline"], "kind": "deadline"}
            return (
                "The latest date I can record is "
                + _d(p["deadline"])
                + ". Could you pay "
                + _m(amount)
                + " on that day?"
            )
        if p.get("offer") and p["offer"].get("date"):
            date_iso = p["offer"]["date"]
        else:
            p["pending_amount"] = amount
            return "On which day can you pay " + _m(amount) + "?"
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
                "The latest date I can record for "
                + _the(q["name"])
                + " is "
                + _d(q["deadline"])
                + ". Could you pay "
                + _m(amt)
                + " on that day?"
            )
        elif res.startswith("not_business_day"):
            nb = res.split(":", 1)[1]
            q["offer"] = {"amount": amt, "date": nb, "kind": "next_business_day"}
            said.append(
                _d(date_iso)
                + " is not a business day. Could you pay "
                + _m(amt)
                + " on the next business day, "
                + _d(nb)
                + "?"
            )
        elif res == "past":
            said.append("The date has to be today or later. On which day can you pay?")
        else:
            said.append("On which exact day can you pay?")
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
                _the(q["name"])
                + ", "
                + _m(q["amount"])
                + " on "
                + _d(q["date"])
                + (
                    ", which is the latest date I can record for it"
                    if q.get("own_deadline")
                    else ""
                )
            )
    return (
        "To confirm: " + "; ".join(parts) + ". " + _line(neg, "confirm_question", "Do you confirm?")
    )


def _advance(st, neg, cal):
    """Move to the next open product, to the read-back, or to the end."""
    j = _next_open(st)
    if j >= 0:
        ack = ""
        just = [q for q in st["p"] if q["status"] == "agreed" and not q.get("acked")]
        if just:
            ack = (
                "I have noted "
                + ", ".join(
                    _the(q["name"]) + " for " + _m(q["amount"]) + " on " + _d(q["date"])
                    for q in just
                )
                + ". "
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
            s = sem.get(q["status"], "refused")
            notes.append([q["id"], rs.get(s, s)])
    st["notes"] = notes if neg.get("notes") else []
    if st["transfer"]:
        st["final_outcome"] = oc.get("transferred", "transferred")
        st["final_reason"] = (neg.get("transfer_reasons") or {}).get(st["transfer"], st["transfer"])
        if not _pol(neg, "notes_on_transfer"):
            st["notes"] = []
        return
    if any(q["status"] == "recorded" for q in st["p"]):
        st["final_outcome"] = oc.get("promise", "promise")
        st["final_reason"] = ""
        return
    left = [sem.get(q["status"], "refused") for q in st["p"] if q["status"] != "paid"]
    if not left:
        st["final_outcome"] = oc.get("paid", "paid")
        st["final_reason"] = ""
        return
    st["final_outcome"] = oc.get("no_promise", "no_promise")
    s = left[0]
    if _pol(neg, "no_promise_reason") == "priority":
        s = next((x for x in _pol(neg, "reason_priority") if x in left), left[0])
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


def _close_hardship(st, neg):
    for q in st["p"]:
        if q["status"] in ("open", "agreed"):
            q["status"] = "hardship"
    _finish(st, neg)


def step(st, r, neg, cal, now, cfg):
    """r: {kind, amount, date, date_kind, all, product}. Mutates and returns st."""
    st["action"] = "say"
    st["mode"] = "speak"
    kind = r.get("kind") or "unclear"
    st["unclear_run"] = int(st.get("unclear_run") or 0) + 1 if kind == "unclear" else 0
    k = st["i"]
    # A product the customer names counts only while it is still open (extraction is noisy).
    if (
        r.get("product") is not None
        and 0 <= r["product"] < len(st["p"])
        and st["p"][r["product"]]["status"] in ("open", "agreed")
    ):
        k = r["product"]
    say = None
    phase = st["phase"]
    if kind == "restructure":
        _transfer(st, "restructuring", neg)
        return st
    if kind == "human":
        st["human_asked"] += 1
        if st["human_asked"] < _pol(neg, "human_requests_to_transfer"):
            st["say"] = _line(
                neg, "help_first", "Before I transfer you, can I help you with this myself?"
            )
            return st
        _transfer(st, "human", neg)
        return st
    named = bool(r.get("date")) or r.get("amount") is not None
    support_offer = _line(
        neg, "support_offer", "Would you like me to connect you with our customer support team?"
    )
    prefix = ""
    if phase == "hardship_offer":
        if kind == "yes":
            _transfer(st, _pol(neg, "hardship_queue"), neg)
            return st
        if kind in ("offer", "vague") or named:
            phase = "offer"  # A32: a date or an amount the customer names is still recorded
            kind = "offer" if named else kind
        elif kind in ("paid", "dispute"):
            phase = "offer"
        elif kind == "unclear":
            # a turn we could not use (often cut off) is no answer: keep the offer open, uncounted
            st["say"] = _sorry(st, r) + " " + support_offer
            return st
        elif kind in ("question", "hardship", "evasive"):
            # a question is not an answer to the offer: answer it and keep the offer open
            st["hardship_repeats"] = int(st.get("hardship_repeats") or 0) + 1
            if st["hardship_repeats"] > 2:
                _close_hardship(st, neg)
                return st
            if kind == "question":
                st["mode"] = "llm"
                st["say"] = (
                    "Answer the customer's last question in one short sentence, only with what "
                    "you know from this call (never offer a discount, a waiver or a plan; if you "
                    'do not have the information, say so). Then say exactly: "'
                    + support_offer
                    + '"'
                )
            else:
                st["say"] = support_offer
            return st
        else:
            _close_hardship(st, neg)
            return st
    elif kind == "hardship":
        mode = _pol(neg, "hardship")
        if mode == "transfer":
            st["final_lines"].append(_line(neg, "sympathy"))
            _transfer(st, _pol(neg, "hardship_queue"), neg)
            return st
        if mode == "close" and not named:
            st["final_lines"].append(_line(neg, "sympathy"))
            _close_hardship(st, neg)
            return st
        sympathy = _line(neg, "sympathy", "I am sorry to hear that.")
        st["hardship"] = True  # A32: no persuasion attempt from now on
        if named:
            # A32: the customer names a date or an amount themselves: handle it as an offer
            kind = "offer"
            prefix = sympathy
        else:
            st["phase"] = "hardship_offer"
            st["say"] = sympathy + " " + support_offer
            return st
    if kind == "stop":
        st["stop"] += 1
        if st["stop"] >= _pol(neg, "stop_requests_to_close"):
            for q in st["p"]:
                if q["status"] in ("open", "agreed"):
                    q["status"] = "refused"
            _finish(st, neg)
            return st
        st["say"] = "I understand. " + _short(st, neg)
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
            _line(neg, "paid", "Payments can take one business day to appear.")
            if kind == "paid"
            else _line(neg, "dispute_noted", "I have noted that.")
        )
        st["say"] = pre + " " + say
        st["last_ask"] = say
        return st
    if kind == "question":
        st["mode"] = "llm"
        st["say"] = (
            "Answer the customer's last question in one short sentence, only with what you "
            "know from this call; if you do not have the information, say so. Then say "
            'exactly: "' + st["last_ask"] + '"'
        )
        return st
    if phase == "closing":
        if kind in ("offer", "vague", "too_much"):
            # A36: a recorded promise cannot be changed in this call
            st["say"] = (
                _line(
                    neg,
                    "cannot_change",
                    "A recorded promise cannot be changed in this call. You can change it in "
                    "the mobile app.",
                )
                + " "
                + st["last_ask"]
            )
            return st
        if kind == "unclear" and r.get("cut") and not st.get("closing_reasked"):
            # one cut-off turn (it may hold a question) gets one more ask; a whole one ("thank
            # you, goodbye") ends the call
            st["closing_reasked"] = True
            st["say"] = _sorry(st, r) + " " + st["last_ask"]
            return st
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
            st["say"] = "Which promise would you like to change?"
            return st
        if kind == "unclear":
            # the read-back was said; saying it whole again and again read as a loop
            st["say"] = _sorry(st, r) + " " + _short(st, neg)
            return st
        st["say"] = _readback(st, neg)
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
            st["say"] = st["last_ask"]
            return st
        say = _advance(st, neg, cal)
    elif kind in ("no", "evasive"):
        # A30: a flat refusal or a non-answer to the payment question uses one attempt
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
            and (
                bool(r.get("all"))
                # an answer to "all of them today?" that names a day and no product means all
                # of them, unless the same message refuses something
                or (phase == "ask_today" and r.get("product") is None and not r.get("mixed"))
            ),
        )
        if say is None:
            say = _advance(st, neg, cal)
        else:
            st["phase"] = "offer"
    elif kind == "too_much":
        if not p["too_much"]:
            p["too_much"] = True
        if (
            not st.get("hardship")
            and p["floor_offered"] < _pol(neg, "floor_offers")
            and p["floor"] < p["full"] - 0.005
        ):
            p["floor_offered"] += 1
            d = (p.get("offer") or {}).get("date") or cal.get("today_iso")
            p["offer"] = {"amount": p["floor"], "date": d, "kind": "floor"}
            say = (
                "The minimum for "
                + _the(p["name"])
                + " is "
                + _m(p["floor"])
                + ". Could you pay "
                + _m(p["floor"])
                + " on "
                + _d(d)
                + "? "
                + _rest(neg)
            )
            st["phase"] = "offer"
        else:
            say = _flat_refusal(st, k, neg, cal)
            if say is None:
                say = _advance(st, neg, cal)
            else:
                st["phase"] = "offer"
    else:
        st["say"] = _sorry(st, r) + " " + _short(st, neg)
        return st
    if say is None:
        if prefix:
            st["final_lines"].insert(0, prefix)
        _finish(st, neg)
        return st
    st["say"] = (prefix + " " + say) if prefix else say
    st["last_ask"] = say
    return st


def _set_rec(st):
    if st["rec"]:
        q = st["p"][st["rec"][0]]
        st["rec_product_id"] = q["id"]
        st["rec_amount"] = q["amount"]
        st["rec_date"] = q["date"]


def record_result(st, ok, neg, cal):
    """After one create_promise call. Retries up to `promise_attempts` calls in total, then
    marks the product failed (and transfers when `record_failure` says so)."""
    j = st["rec"][0]
    q = st["p"][j]
    if ok:
        q["status"] = "recorded"
        st["rec"].pop(0)
        st["rec_try"] = 0
    else:
        st["rec_try"] += 1
        if st["rec_try"] >= int(_pol(neg, "promise_attempts")):
            q["status"] = "failed"
            st["rec"].pop(0)
            st["rec_try"] = 0
            if _pol(neg, "record_failure") == "transfer":
                st["rec"] = []
                _transfer(st, _pol(neg, "record_failure_queue"), neg)
                return st
    if st["rec"]:
        st["action"] = "record"
        _set_rec(st)
        return st
    rec = [q for q in st["p"] if q["status"] == "recorded"]
    bad = [q for q in st["p"] if q["status"] == "failed"]
    parts = []
    if rec:
        parts.append(
            "Your promise is recorded: "
            + "; ".join(
                _the(q["name"]) + ", " + _m(q["amount"]) + " on " + _d(q["date"]) for q in rec
            )
            + ". "
            + _line(neg, "recorded", "You will receive a text message confirming this promise.")
        )
    if bad:
        parts.append(
            "I could not record the promise for " + ", ".join(_the(q["name"]) for q in bad) + "."
        )
    anything = _line(neg, "anything_else", "Is there anything else I can help you with?")
    parts.append(anything)
    st["action"] = "say"
    st["mode"] = "speak"
    st["phase"] = "closing"
    st["say"] = " ".join(parts)
    st["last_ask"] = anything
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
    "has_question",
)


def answer_first(st, r):
    """The customer also asked something (a robot, a discount, ...): the model answers it in
    one sentence, then says the engine's exact sentence."""
    if (
        r.get("question")
        and r.get("kind") != "question"
        and st.get("action") == "say"
        and st.get("mode", "speak") == "speak"
        and st.get("say")
    ):
        st["mode"] = "llm"
        st["say"] = (
            "First answer the customer's question in one short sentence, only with what you "
            "know from this call (never offer a discount, a waiver or a plan; if you do not "
            'have the information, say so). Then say exactly: "' + st["say"] + '"'
        )
    return st


def last_customer_text(v):
    """The customer's last message word for word. Code nodes get the conversation as
    vars['system.conversation'] (a list of role/content entries, measured 11 Oct); empty
    when it is missing (local tests)."""
    conv = v.get("system.conversation")
    if isinstance(conv, list):
        for m in reversed(conv):
            if isinstance(m, dict) and m.get("role") == "user":
                return str(m.get("content") or "")
    return ""


# The customer means every product: read from the words, because the all_products extraction
# was missed once on "I would like to pay both the credit card and the loan on the 30th".
ALL_WORDS = re.compile(r"\b(both|all of them|all (two|three|four|five|2|3|4|5)( of them)?)\b", re.I)
# A refusal in the same message ("the card tomorrow, but I am not paying the loan"): an offer
# that names no product must not go to every product.
REFUSAL_WORDS = re.compile(r"\b(not|never|refuse|won't|can't|cannot|don't)\b|n't\b", re.I)


END_MARK = re.compile(r"[.!?…\"')\]]\s*$")


def is_cut(raw):
    """The simulator often sends a customer turn cut off mid-sentence (no end mark), up to 13
    in a row with a talkative customer; a turn with an end mark is whole."""
    t = str(raw or "").strip()
    return bool(t) and not END_MARK.search(t)


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
    if kind in ("yes", "no", "too_much", "evasive") and (iso or amount is not None):
        kind = "offer"  # a date or an amount the customer names is never a refusal
    if kind == "offer" and not iso and amount is None and dk == "vague":
        kind = "vague"
    prod = str(v.get("about_product") or "").strip().lower()
    # "p1, p2" (seen once) is not a single product: take the first one named
    codes = re.findall(r"p([1-9])", prod)
    pi = int(codes[0]) - 1 if codes else None
    raw = last_customer_text(v)
    allp = str(v.get("all_products") or "").strip().lower() in ("yes", "true", "all")
    if raw and ALL_WORDS.search(raw):
        allp = True
    cut = is_cut(raw)
    if cut and kind in ("stop", "evasive") and not iso and amount is None:
        # half a sentence is no wish to end the call and no non-answer: nothing is counted
        kind = "unclear"
    return {
        "kind": kind,
        "amount": amount,
        "date": iso,
        "date_kind": dk,
        "all": allp,
        "product": pi,
        "mixed": bool(raw and REFUSAL_WORDS.search(raw)),
        "cut": cut,
        "question": str(v.get("has_question") or "").strip().lower() in ("yes", "true"),
    }


def clear_reply(v):
    for k in REPLY_VARS:
        v[k] = ""


def publish(v, st, neg):
    v["neg_state"] = json.dumps(st)
    v["neg_action"] = st.get("action") or "say"
    v["neg_say"] = st.get("say") or ""
    # speak: neg_say is the exact sentence (spoken as is); llm: an instruction for the model
    v["neg_mode"] = st.get("mode") or "speak"
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
