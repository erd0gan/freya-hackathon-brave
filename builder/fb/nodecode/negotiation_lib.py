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


def close_reason(neg, sem):
    """close_call's reason for a semantic reason. `neg.reasons` holds add_note's values; when the
    brief's close_call uses other values for the same reason, `neg.close_reasons` holds those
    (review: add_note `refused`, close_call `declined_payment`)."""
    return str(
        (neg.get("close_reasons") or {}).get(sem) or (neg.get("reasons") or {}).get(sem) or sem
    )


def call_ok(value, mode="flag"):
    """Did a write succeed, from the response field the workflow mapped. "flag": a true/false
    field (ok); "present": a field only a success returns (promise_id); "no_error": an error
    field, empty on success. A tool API without an ok field must not read as failed every
    time (review: create_promise answering only promise_id)."""
    v = str(value if value is not None else "").strip().lower()
    if mode == "present":
        return v not in ("", "false", "none", "null", "0")
    if mode == "no_error":
        return v in ("", "none", "null", "false")
    return v == "true"


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
            a = str(pf.get(key + "_attempts") if pf.get(key + "_attempts") is not None else "")
            att = int(a) if a.strip().isdigit() else 1  # 0 is a band with no persuasion
        except Exception:
            att = 1
        deadline = str(pf.get(key + "_deadline") or "")
        try:
            datetime.date.fromisoformat(deadline[:10])
        except Exception:
            deadline = ""
        if full <= 0.005 or not deadline or not pf.get(key + "_id"):
            # no amount, no deadline (a product outside every band) or no id: nothing about it
            # can be offered or recorded (review: a missing past_due was offered as 0.00)
            continue
        floor = min(floor, full) if floor > 0.005 else full
        ps.append(
            {
                "id": pf.get(key + "_id") or key,
                "name": pf.get(key + "_name") or "",
                "full": full,
                "floor": floor,
                "deadline": deadline,
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
    order = str(neg.get("product_order") or "get_account")
    for n, q in enumerate(ps):
        q["n"] = n  # get_account order, for "first_product"
    if order == "days_late_desc":
        ps.sort(key=lambda q: -_num(q.get("days_late")))
    elif order == "past_due_desc":
        ps.sort(key=lambda q: -q["full"])
    if not ps:
        # get_account failed or gave nothing usable: no negotiation on made-up values
        st["phase"] = "closing"
        st["action"] = "finish"
        st["final_lines"] = [
            _line(neg, "no_account", "I am sorry, I cannot see your account details right now.")
        ]
        st["final_outcome"] = (neg.get("outcomes") or {}).get("no_promise", "no_promise")
        st["final_reason"] = close_reason(neg, "service_error")
        return st
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
        return _line(neg, "support_reask", "Would you like that?")
    if phase == "offer":
        # From the second unusable turn in a row, leave room for a counter-day: a customer who
        # wants another day was cut off 20-30 times in a row on "Can you do that?" (PUB-06,
        # talkative) until the call ran out of time.
        if int(st.get("unclear_run") or 0) >= 2:
            return _line(
                neg, "offer_day_question", "Can you do that, or is another day better for you?"
            )
        return "Can you do that?"
    if len(st.get("p") or []) > 1:
        return _line(neg, "multi_today_question", "Can you pay all of them today?")
    return _line(neg, "today_question", "Can you make this payment today?")


def _rest(neg):
    return _line(neg, "rest_overdue", "The rest stays overdue.")


def _num(x):
    try:
        return float(x)
    except Exception:
        return 0.0


def _days(p):
    return p.get("days_late", "") if p.get("days_late") not in (None, "") else ""


def _current_amount(p):
    return p["floor"] if p["too_much"] else p["full"]


def _amount_for(p, amount):
    """The amount a turn is about. A turn that names only a day keeps the amount said or
    offered before it ("200" ... "Friday" is 200 on Friday, not the full amount)."""
    if amount is not None:
        return amount
    if p.get("pending_amount") is not None:
        return p["pending_amount"]
    if p.get("offer"):
        return p["offer"]["amount"]
    if p.get("amount") is not None:
        return p["amount"]
    return _current_amount(p)


def _valid_day(q, d, now, cfg, cal):
    """(a day the bank may record for product q, why it differs from d). Every offer and every
    record goes through the date rules: today or later, a business day, not after the
    deadline."""
    res = check_promise_date(d, q["deadline"], now, cfg) if d else "unreadable"
    if res == "ok":
        return d, ""
    if res.startswith("not_business_day"):
        return res.split(":", 1)[1], "not_business_day"
    if res.startswith("after_deadline"):
        return q["deadline"], "deadline"
    for c in (cal.get("today_iso"), cal.get("attempt1_date")):
        if c and check_promise_date(c, q["deadline"], now, cfg) == "ok":
            return c, "past"
    return q["deadline"], "deadline"


def _invalid_agreed(st, now, cfg, cal):
    """The first agreed product whose amount or day breaks the rules, with fixed values; the
    last check before anything reaches the bank."""
    for j, q in enumerate(st["p"]):
        if q["status"] != "agreed":
            continue
        amt = q.get("amount")
        ok_amt = amt is not None and q["floor"] - 0.005 <= float(amt) <= q["full"] + 0.005
        d, why = _valid_day(q, q.get("date"), now, cfg, cal)
        if ok_amt and not why:
            continue
        amt = q["full"] if amt is None else min(max(float(amt), q["floor"]), q["full"])
        return j, round(amt, 2), d
    return None


def _attempt_offer(st, k, neg, cal):
    p = st["p"][k]
    st["i"] = k
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
    if _was_refused(p, amount, date):
        # A31 (PUB-10: the floor refused as attempt 2 came again after "too much")
        p["offer"] = None
        return None
    p["offer"] = {"amount": amount, "date": date, "kind": "attempt"}
    p["pending_amount"] = None
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


def _refuse_offer(p, amount=None, date=None):
    """A31: an offer turned down (default: the one on the table) is never made again."""
    o = p.get("offer") or {}
    a = o.get("amount") if amount is None else amount
    d = o.get("date") if date is None else date
    if a is None or not d:
        return
    p["refused_offers"] = list(p.get("refused_offers") or []) + [[round(float(a), 2), str(d)]]


def _was_refused(p, amount, date):
    for x in p.get("refused_offers") or []:
        try:
            if abs(float(x[0]) - float(amount)) < 0.005 and str(x[1]) == str(date):
                return True
        except Exception:
            continue
    return False


def _flat_refusal(st, k, neg, cal):
    p = st["p"][k]
    if st.get("hardship"):
        # A32: after hardship there is no persuasion attempt; the product ends as hardship
        p["status"] = "hardship"
        p["offer"] = None
        return None
    while p["used"] < p["attempts"]:
        p["used"] += 1
        say = _attempt_offer(st, k, neg, cal)
        if say:
            return say
    p["status"] = "refused"
    p["offer"] = None
    return None


def _agree(p, amount, date, own_deadline=False):
    p["status"] = "agreed"
    p["amount"] = round(float(amount), 2)
    p["date"] = date
    p["offer"] = None
    p["pending_amount"] = None
    p["own_deadline"] = own_deadline
    p["acked"] = False


def _apply_offer(st, k, amount, date_iso, date_kind, neg, cal, now, cfg, all_products):
    """The customer named an amount and/or a date for product k (or for all)."""
    p = st["p"][k]
    st["i"] = k  # the next yes answers a question about this product
    targets = (
        [j for j, q in enumerate(st["p"]) if q["status"] in ("open", "agreed")]
        if all_products
        else [k]
    )
    amount = _amount_for(p, amount)
    if amount > p["full"] + 0.005:
        amount = p["full"]
    if amount < p["floor"] - 0.005 and len(targets) == 1:
        if p["floor_offered"] >= _pol(neg, "floor_offers"):
            return _flat_refusal(st, k, neg, cal)
        # the floor keeps the date rules: a day after the deadline or in the past is not offered
        d0 = date_iso or (p["offer"] or {}).get("date") or cal.get("today_iso")
        d, why = _valid_day(p, d0, now, cfg, cal)
        if _was_refused(p, p["floor"], d):
            return _flat_refusal(st, k, neg, cal)  # A31: that floor offer was turned down
        p["floor_offered"] += 1
        p["offer"] = {"amount": p["floor"], "date": d, "kind": "floor"}
        p["pending_amount"] = None
        lead = ""
        if date_iso and why == "deadline":
            lead = "The latest date I can record is " + _d(d) + ". "
        elif date_iso and why == "not_business_day":
            lead = _d(date_iso) + " is not a business day. "
        return (
            "I cannot record "
            + _m(amount)
            + ". The minimum is "
            + _m(p["floor"])
            + ". "
            + lead
            + "Could you pay "
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
                # "both next week": the exact day that answers this is for all of them too
                st["vague_all"] = len(targets) > 1
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
    # Every product the day is for is checked first; a counter-offer for one of them must not
    # drop the day for the others (review: "both on the 26th" lost the loan's own date).
    said = []
    for j in targets:
        q = st["p"][j]
        amt = amount if j == k else _amount_for(q, None)
        res = check_promise_date(date_iso, q["deadline"], now, cfg)
        line = ""
        if res == "ok":
            _agree(q, amt, date_iso)
        elif res.startswith("after_deadline") and len(targets) > 1:
            _agree(q, amt, q["deadline"], own_deadline=True)
        elif res.startswith("after_deadline"):
            line = (
                "The latest date I can record for "
                + _the(q["name"])
                + " is "
                + _d(q["deadline"])
                + ". Could you pay "
                + _m(amt)
                + " on that day?"
            )
            q["offer"] = {"amount": amt, "date": q["deadline"], "kind": "deadline", "say": line}
        elif res.startswith("not_business_day"):
            nb = res.split(":", 1)[1]
            line = (
                _d(date_iso)
                + " is not a business day. Could you pay "
                + _m(amt)
                + (" for " + _the(q["name"]) if len(targets) > 1 else "")
                + " on the next business day, "
                + _d(nb)
                + "?"
            )
            q["offer"] = {"amount": amt, "date": nb, "kind": "next_business_day", "say": line}
        elif res == "past":
            q["pending_amount"] = amt
            line = "The date has to be today or later. On which day can you pay?"
        else:
            q["pending_amount"] = amt
            line = "On which exact day can you pay?"
        if line:
            said.append((j, line))
    if said:
        st["i"] = said[0][0]
        return said[0][1]
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


def _ack(st):
    """'I have noted ...' for the products agreed since the last time it was said."""
    just = [q for q in st["p"] if q["status"] == "agreed" and not q.get("acked")]
    if not just:
        return ""
    for q in just:
        q["acked"] = True
    return (
        "I have noted "
        + ", ".join(
            _the(q["name"]) + " for " + _m(q["amount"]) + " on " + _d(q["date"]) for q in just
        )
        + ". "
    )


def _to_readback(st, neg):
    if any(q["status"] == "agreed" for q in st["p"]):
        st["phase"] = "confirm"
        return _readback(st, neg)
    return None


def _hardship_rest(st, neg):
    """A32: no attempt after hardship. The support offer is made once; otherwise the open
    products end as hardship."""
    if _pol(neg, "hardship") == "offer_support" and not st.get("support_offered"):
        st["support_offered"] = True
        st["phase"] = "hardship_offer"
        return _ack(st) + _line(
            neg, "support_offer", "Would you like me to connect you with our customer support team?"
        )
    for q in st["p"]:
        if q["status"] == "open":
            q["status"] = "hardship"
            q["offer"] = None
    return _to_readback(st, neg)


def _advance(st, neg, cal):
    """Move to the next open product, to the read-back, or to the end."""
    while _next_open(st) >= 0:
        j = _next_open(st)
        p = st["p"][j]
        st["i"] = j
        if (p.get("offer") or {}).get("say"):
            # the customer's own day for this product needs its own answer (holiday, deadline)
            st["phase"] = "offer"
            return _ack(st) + p["offer"]["say"]
        if st.get("hardship"):
            return _hardship_rest(st, neg)
        if p["used"] == 0:
            p["used"] = 1
        say = _attempt_offer(st, j, neg, cal) or _flat_refusal(st, j, neg, cal)
        if say:
            st["phase"] = "offer"
            return _ack(st) + say
    return _to_readback(st, neg)


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
    recorded = set(st.get("recorded") or [])
    for q in st["p"]:
        if q["id"] in recorded:
            q["status"] = "recorded"  # a promise the bank holds is never undone by later words
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
    by_account = sorted(st["p"], key=lambda q: q.get("n", 0))
    left = [sem.get(q["status"], "refused") for q in by_account if q["status"] != "paid"]
    if not left:
        st["final_outcome"] = oc.get("paid", "paid")
        # only a brief whose outcome table gives "already paid" a reason sets close_reasons.paid
        st["final_reason"] = str((neg.get("close_reasons") or {}).get("paid") or "")
        return
    st["final_outcome"] = oc.get("no_promise", "no_promise")
    s = left[0]
    if _pol(neg, "no_promise_reason") == "priority":
        s = next((x for x in _pol(neg, "reason_priority") if x in left), left[0])
    st["final_reason"] = close_reason(neg, s)


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


def _close_hardship(st, neg, lead=""):
    """Open products end as hardship; an agreed promise is still read back (A32)."""
    for q in st["p"]:
        if q["status"] == "open":
            q["status"] = "hardship"
            q["offer"] = None
    say = _to_readback(st, neg)
    if say is None:
        if lead:
            st["final_lines"].append(lead)
        _finish(st, neg)
        return
    st["say"] = (lead + " " + say) if lead else say
    st["last_ask"] = say


# A day number: digits or a spoken ordinal ("the twenty-seventh").
DAY_NUMBER = re.compile(
    r"\d|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth|"
    r"teenth|tieth",
    re.I,
)


# Words that move a weekday to another week ("next Friday", "the following Tuesday").
MOVE_WORDS = re.compile(r"\b(next|following|after|week)\b", re.I)


NAME_SKIP = ("ending", "account", "the", "your", "and")


def _name_words(q):
    return [
        w
        for w in re.findall(r"[a-z]{3,}|\d{4}", str(q.get("name") or "").lower())
        if w not in NAME_SKIP
    ]


def _named_in(st, text):
    """Products named: whole name, then a word only it has, then a shared word."""
    t = " " + str(text or "").lower() + " "
    found = []
    for i, q in enumerate(st["p"]):
        nm = str(q.get("name") or "").lower().strip()
        if nm and nm in t:
            found.append(i)
            t = t.replace(nm, " ")
    words = [_name_words(q) for q in st["p"]]

    def said(ws):
        return any(re.search(r"\b" + w + r"\b", t) for w in ws)

    for i, ws in enumerate(words):
        own = [w for w in ws if not any(w in o for n, o in enumerate(words) if n != i)]
        if i not in found and said(own):
            found.append(i)
    for i, ws in enumerate(words):
        if i not in found and said(ws):
            found.append(i)
    return sorted(found)


def _yes_part(raw):
    """The words before a refusal ("Yes for the card, but I won't pay the loan")."""
    raw = str(raw or "")
    masked = TODAY_REFUSAL.sub(lambda m: " " * len(m.group(0)), raw)
    m = REFUSAL_WORDS.search(masked)
    return raw[: m.start()] if m else raw


def _yes_products(st, r, among=("open",)):
    """The products (status in `among`) a yes is for, or None for all. With a refusal in the
    message, the ones named before it (review: names inside the refusal agreed all three)."""
    n = len(st["p"])
    j = r.get("product")
    j = j if j is not None and 0 <= j < n and st["p"][j]["status"] in among else None
    raw = str(r.get("raw") or "").lower()
    if r.get("mixed") and raw:
        yes = _yes_part(raw)
        named = [i for i in _named_in(st, yes) if st["p"][i]["status"] in among]
        if named:
            return named
        no = _named_in(st, raw[len(yes) :])
        if no:
            return [i for i, q in enumerate(st["p"]) if q["status"] in among and i not in no]
        if j is not None:
            return [j]
        cur = st.get("i", 0)
        return [cur] if 0 <= cur < n and st["p"][cur]["status"] in among else None
    if j is None or r.get("all"):
        return None
    if any(i != j and st["p"][i]["status"] in among for i in _named_in(st, raw)):
        return None
    return [j]


def _yes_to_offered_day(p, r):
    """A yes naming only the weekday of the day on the table (the offer, or the agreed day at
    the read-back) means that day, not the nearest one (A PUB-07). True: a plain yes to it."""
    o = p.get("offer") or {}
    day = o.get("date") or (p.get("date") if p.get("status") == "agreed" else None)
    if not (r.get("said_yes") and day and r.get("date")):
        return False
    if r["date"] != day:
        text = str(r.get("date_text") or "")
        if DAY_NUMBER.search(text):
            return False
        if MOVE_WORDS.search(text):
            return False  # "Yes, next Friday" to "Friday 16 October" names another Friday
        try:
            same = (
                datetime.date.fromisoformat(r["date"]).weekday()
                == datetime.date.fromisoformat(day).weekday()
            )
        except Exception:
            return False
        if not same:
            return False
        r["date"] = day
    return r.get("amount") is None


# Customers give a number in two parts (kit README, style `pieces`): "six hundred and" ...
JOINERS = ("and", "point", "dot", "plus")


def _amount_unfinished(text, raw):
    """Ends in a joiner, or the turn was cut right after it."""
    words = re.findall(r"[a-z0-9]+", str(text or "").lower())
    if words and words[-1] in JOINERS:
        return True
    raw = str(raw or "").strip().lower()
    if not raw or not is_cut(raw):
        return False
    rw = re.findall(r"[a-z0-9]+", raw)
    if rw and rw[-1] in JOINERS:
        rw = rw[:-1]
    return bool(words and rw and rw[-1] == words[-1])


def _join_amount(part, text):
    """A rest as big as the part is the amount said again; digits add up."""
    a = parse_amount(part)
    b = parse_amount(text)
    if a is None or b is None or b >= a:
        return b if b is not None else a
    joined = parse_amount(part + " " + text)
    if joined is not None and a < joined < 2 * a and abs(joined * 100 - round(joined * 100)) < 1e-6:
        return joined
    return round(a + b, 2)


def _pieces(st, r, kind):
    """Joins an amount said over two turns ("six hundred and" / "thirty-seven fifty").
    Returns the kind, or None to wait for the rest."""
    part = str(st.get("amount_part") or "")
    text = str(r.get("amount_text") or "").strip()
    if part:
        if text:
            a = _join_amount(part, text)
            if a is not None:
                r["amount"] = a
                text = str(a) + (" and" if _amount_unfinished(text, "") else "")
        elif kind == "unclear":
            return kind  # still nothing usable: the first part stays
        elif r.get("amount") is None and kind in ("offer", "vague"):
            r["amount"] = parse_amount(part)  # the rest never came: the part is the amount
        st["amount_part"] = ""
        if r.get("amount") is not None:
            if not r.get("date") and st.get("part_date"):
                r["date"] = st["part_date"]
            if kind in ("yes", "no", "too_much", "evasive", "vague", "unclear"):
                kind = "offer"
        st["part_date"] = ""
    if (
        kind == "offer"
        and text
        and r.get("amount") is not None
        and int(st.get("parts") or 0) < 2
        and _amount_unfinished(text, r.get("raw"))
    ):
        st["parts"] = int(st.get("parts") or 0) + 1
        st["amount_part"] = text
        st["part_date"] = r.get("date") or ""
        st["say"] = CUT_SORRY + " Please go on."
        return None
    st["parts"] = 0
    return kind


def step(st, r, neg, cal, now, cfg):
    """r: {kind, amount, date, date_kind, all, product}. Mutates and returns st."""
    st["action"] = "say"
    st["mode"] = "speak"
    kind = r.get("kind") or "unclear"
    if kind == "unclear" and r.get("cut_kind") and int(st.get("unclear_run") or 0) >= 2:
        # the third cut-off turn in a row is taken as said
        kind = r["cut_kind"]
    kind = _pieces(st, r, kind)
    if kind is None:
        return st  # half an amount: wait for the rest
    st["unclear_run"] = int(st.get("unclear_run") or 0) + 1 if kind == "unclear" else 0
    vague_all = bool(st.get("vague_all"))
    if kind not in ("unclear", "question"):
        st["vague_all"] = False
    phase = st["phase"]
    named_j = r.get("product")
    if named_j is not None and not 0 <= named_j < len(st["p"]):
        named_j = None
    k = st["i"]
    if named_j is not None and st["p"][named_j]["status"] in ("open", "agreed"):
        k = named_j
    elif (
        named_j is not None
        and kind in ("offer", "vague")
        and phase != "recording"
        and st["p"][named_j]["status"] in ("refused", "hardship", "paid", "dispute")
    ):
        # A29: an offer for a product refused before reopens that product
        q = st["p"][named_j]
        q["status"] = "open"
        q["offer"] = None
        q["pending_amount"] = None
        k = named_j
        if phase == "closing":
            phase = "offer"
            st["phase"] = "offer"
    say = None
    pk = st["p"][k]
    if r.get("amount") is not None and r["amount"] < pk["floor"] - 0.005 and r.get("amount_text"):
        # "three fifty" for a 420 debt with a 126 floor means 350, not 3.50
        alt = parse_amount(r["amount_text"], pk["floor"], pk["full"])
        if alt is not None:
            r["amount"] = alt
    if _yes_to_offered_day(st["p"][k], r) and kind == "offer" and phase in ("offer", "confirm"):
        kind = "yes"  # "Yes, that Friday" names the day on the table
    if kind == "restructure":
        _transfer(st, "restructuring", neg)
        return st
    help_line = _line(neg, "help_first", "Before I transfer you, can I help you with this myself?")
    if st.get("help_open"):
        # an answer to help_first, not to the question before it
        if kind == "unclear":
            st["say"] = _sorry(st, r) + " " + help_line
            return st
        if kind == "question":
            st["mode"] = "llm"
            st["say"] = (
                "Answer the customer's last question in one short sentence, only with what you "
                "know from this call; if you do not have the information, say so. Then say "
                'exactly: "' + help_line + '"'
            )
            return st
        st["help_open"] = False
        if kind == "yes":
            st["say"] = _short(st, neg) if phase == "ask_today" else st["last_ask"]
            return st
        if kind == "no":
            kind = "human"  # no, you cannot help: the customer insists
    if kind == "human":
        st["human_asked"] += 1
        if st["human_asked"] < _pol(neg, "human_requests_to_transfer"):
            st["help_open"] = True
            st["say"] = help_line
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
            # no answer: the offer stays open, uncounted, re-asked short (A PUB-05)
            st["say"] = _sorry(st, r) + " " + _short(st, neg)
            return st
        elif kind in ("question", "hardship", "evasive"):
            # a question is no answer: answer it, the offer stays open; non-answers are counted
            if kind != "question":
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
                st["say"] = _short(st, neg)
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
            st["hardship"] = True
            _close_hardship(st, neg, _line(neg, "sympathy", "I am sorry to hear that."))
            return st
        sympathy = _line(neg, "sympathy", "I am sorry to hear that.")
        st["hardship"] = True  # A32: no persuasion attempt from now on
        if named:
            # A32: the customer names a date or an amount themselves: handle it as an offer
            kind = "offer"
            prefix = sympathy
        else:
            st["support_offered"] = True
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
        # a claim is about the product it names (refused -> paid), never moved to another one
        settled = ("recorded", "failed")
        t = named_j if named_j is not None else k
        if phase == "recording" or (phase == "closing" and named_j is None):
            _finish(st, neg)  # said after the promises were recorded: the bank's stay as they are
            return st
        if st["p"][t]["status"] in settled or (
            named_j is None and st["p"][t]["status"] in ("paid", "dispute")
        ):
            left = [j for j, q in enumerate(st["p"]) if q["status"] in ("open", "agreed")]
            if named_j is not None or not left:
                if phase == "closing":
                    _finish(st, neg)
                    return st
                st["say"] = _line(neg, "dispute_noted", "I have noted that.") + " " + st["last_ask"]
                return st
            t = left[0]
        p = st["p"][t]
        p["status"] = kind
        p["offer"] = None
        pre = (
            _line(neg, "paid", "Payments can take one business day to appear.")
            if kind == "paid"
            else _line(neg, "dispute_noted", "I have noted that.")
        )
        if phase == "closing":
            # after the recording: noted, the call goes on
            st["say"] = pre + " " + st["last_ask"]
            return st
        say = _advance(st, neg, cal) if len(st["p"]) > 1 else None
        if say is None:
            if kind == "paid":
                # at the goodbye only when not said in a turn
                st["final_lines"].append(pre)
            _finish(st, neg)
            return st
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
            # one cut-off turn gets one more ask; a whole one ends the call
            st["closing_reasked"] = True
            st["say"] = _sorry(st, r) + " " + st["last_ask"]
            return st
        _finish(st, neg)
        return st
    if phase == "confirm":
        agreed = [j for j, q in enumerate(st["p"]) if q["status"] == "agreed"]
        if (
            st.pop("which_open", False)
            and named_j in agreed
            and kind in ("unclear", "no", "yes", "evasive")
            and not r.get("date")
            and r.get("amount") is None
        ):
            # "Which promise ...?" answered with the product alone ("The loan.")
            kind = "no"
        only = _yes_products(st, r, ("agreed",)) if kind == "yes" and len(agreed) > 1 else None
        if only and len(only) < len(agreed):
            # "Yes, but only the card": a no to the others, each gets its next attempt
            say = None
            for j in agreed:
                if j not in only:
                    q = st["p"][j]
                    _refuse_offer(q, q["amount"], q["date"])
                    q["status"] = "open"
            for j in agreed:
                if j not in only and say is None:
                    say = _flat_refusal(st, j, neg, cal)
            if say:
                st["phase"] = "offer"
                st["say"] = say
                st["last_ask"] = say
                return st
        if kind == "yes":
            bad = _invalid_agreed(st, now, cfg, cal)
            if bad is not None:
                # nothing outside the rules reaches the bank: offer the nearest allowed values
                j, amt, d = bad
                q = st["p"][j]
                q["status"] = "open"
                q["offer"] = {"amount": amt, "date": d, "kind": "fixed"}
                st["i"] = j
                st["phase"] = "offer"
                say = "Could you pay " + _m(amt) + " for " + _the(q["name"]) + " on " + _d(d) + "?"
                if amt < q["full"] - 0.005:
                    say += " " + _rest(neg)
                st["say"] = say
                st["last_ask"] = say
                return st
            st["rec"] = [j for j, q in enumerate(st["p"]) if q["status"] == "agreed"]
            st["rec_batch"] = [st["p"][j]["id"] for j in st["rec"]]
            st["rec_try"] = 0
            st["action"] = "record"
            st["phase"] = "recording"
            _set_rec(st)
            return st
        if kind in ("offer", "vague", "too_much") and (agreed or st["p"][k]["status"] == "open"):
            # A35: a change reopens the product(s) it is about; a new read-back follows
            every = (
                kind != "too_much"
                and len(agreed) > 1
                and bool(r.get("all"))
                and not r.get("mixed")
                and r.get("amount") is None
            )
            if st["p"][k]["status"] != "open":
                k = k if k in agreed else agreed[0]
                for j in agreed if every else [k]:
                    q = st["p"][j]
                    q["status"] = "open"
                    if kind == "too_much":
                        _refuse_offer(q, q["amount"], q["date"])
                    elif r.get("amount") is not None and not r.get("date") and q.get("date"):
                        # "Make it 200 instead": the agreed day stays
                        q["offer"] = {"amount": q["amount"], "date": q["date"], "kind": "readback"}
            phase = "offer"
            st["phase"] = "offer"
            st["i"] = k
        elif kind == "no":
            named = r.get("product")
            if named in agreed:
                which = [named]
            elif named_j is not None:
                # a no about a product not read back: ask again
                st["say"] = "I understand. " + _readback(st, neg)
                return st
            elif len(agreed) == 1:
                which = agreed
            elif not st.get("which_asked"):
                st["which_asked"] = True
                st["which_open"] = True
                st["say"] = _line(neg, "which_change", "Which promise would you like to change?")
                return st
            else:
                which = agreed  # a no again and no product named: a no to all of them
            for j in which:
                q = st["p"][j]
                _refuse_offer(q, q["amount"], q["date"])
                q["status"] = "open"
            say = _flat_refusal(st, which[0], neg, cal)
            if say is None:
                say = _advance(st, neg, cal)
            if say is None:
                _finish(st, neg)
                return st
            if any(q["status"] == "open" for q in st["p"]):
                st["phase"] = "offer"
            st["say"] = say
            st["last_ask"] = say
            return st
        elif kind == "unclear":
            # the read-back was said; saying it whole again and again read as a loop
            st["say"] = _sorry(st, r) + " " + _short(st, neg)
            return st
        else:
            st["say"] = _readback(st, neg)
            return st
    # ask_today or offer
    p = st["p"][k]
    multi = len(st["p"]) > 1
    if kind == "yes":
        if phase == "ask_today":
            only = _yes_products(st, r, ("open",))
            for j, q in enumerate(st["p"]):
                if q["status"] == "open" and (only is None or j in only):
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
        first = phase == "ask_today" and multi
        _refuse_offer(p)
        say = _flat_refusal(st, k, neg, cal)
        if say is None:
            say = _advance(st, neg, cal)
        else:
            st["phase"] = "offer"
        if first and say:
            # B4: "all of them today?" was no attempt on each product
            say = _line(neg, "one_by_one", "Let us go through them one at a time.") + " " + say
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
            and not r.get("mixed")
            and (
                bool(r.get("all"))
                # a day and no product, to "all of them today?"
                or (phase == "ask_today" and r.get("product") is None and not r.get("mixed"))
                # the exact day after "both next week"
                or (vague_all and r.get("product") is None)
            ),
        )
        if say is None:
            say = _advance(st, neg, cal)
        else:
            st["phase"] = "offer"
    elif kind == "too_much":
        if not p["too_much"]:
            p["too_much"] = True
        _refuse_offer(p)
        d0 = (p.get("offer") or {}).get("date") or cal.get("today_iso")
        d, _why = _valid_day(p, d0, now, cfg, cal)
        if (
            not st.get("hardship")
            and p["floor_offered"] < _pol(neg, "floor_offers")
            and p["floor"] < p["full"] - 0.005
            and not _was_refused(p, p["floor"], d)
        ):
            p["floor_offered"] += 1
            p["offer"] = {"amount": p["floor"], "date": d, "kind": "floor"}
            p["pending_amount"] = None
            st["i"] = k
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
        st["recorded"] = list(st.get("recorded") or []) + [q["id"]]
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
    # this read-back's products only: one recorded earlier in the call was said then
    batch = st.get("rec_batch")
    rec = [q for q in st["p"] if q["status"] == "recorded" and (batch is None or q["id"] in batch)]
    bad = [q for q in st["p"] if q["status"] == "failed" and (batch is None or q["id"] in batch)]
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
# "I cannot pay (it) today", "can't do it today", "won't be able to pay today", "Not today, but
# ...": a no to today, not to a product.
TODAY_REFUSAL = re.compile(
    r"\bnot\s+(for\s+)?today\b|"
    r"\b(i\s+)?(cannot|can't|can not|won't|will not|am not able to|couldn't|could not|"
    r"unable to|won't be able to|will not be able to|not be able to|don't think i can)\s+"
    r"(pay|make|do|manage)(\s+\w+){0,3}?\s+today\b",
    re.I,
)
REFUSAL_WORDS = re.compile(r"\b(not|never|refuse|won't|can't|cannot|don't)\b|n't\b", re.I)


# A wish to end the call that is whole without an end mark.
STOP_WORDS = re.compile(
    r"\b(do not|don't|dont) want to (talk|speak|continue)|stop calling|leave me alone|"
    r"not interested|hang(ing)? up|goodbye\b",
    re.I,
)
# Refusing to answer a security question, whole even without an end mark.
REFUSE_ANSWER = re.compile(
    r"\b(won't|will not|not going to|refuse to|don't want to|do not want to)\s+"
    r"(give|tell|say|answer|share)\b|\bnot (giving|telling|sharing)\b",
    re.I,
)
# Asking what the call is about instead of answering a security question (a refusal to answer).
ABOUT_WORDS = re.compile(
    r"\b(what|why)('s| is| are)? (this|it|the call|you calling)( about| for)?\b|"
    r"\bwhy (do|would) you (need|want)\b|\bwho (is this|are you)\b",
    re.I,
)
# Words of a number in an amount text.
AMOUNT_WORDS = re.compile(
    r"\d|\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|thirty|"
    r"forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|grand)\b",
    re.I,
)
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
    amount_text = str(v.get("said_amount_text") or "")
    amount = parse_amount(amount_text)
    if amount is None and AMOUNT_WORDS.search(amount_text):
        pass  # a number we cannot read ('200 or maybe 300'): asked again, not the model's guess
    elif amount is None:
        try:
            amount = (
                float(v.get("said_amount")) if str(v.get("said_amount") or "").strip() else None
            )
        except Exception:
            amount = None
    said_yes = kind == "yes"
    if kind in ("yes", "no", "too_much", "evasive") and (iso or amount is not None):
        kind = "offer"  # a date or an amount the customer names is never a refusal
    if kind == "offer" and not iso and amount is None and dk == "vague":
        kind = "vague"
    prod = str(v.get("about_product") or "").strip().lower()
    # "p1, p2" (seen once) is not a single product: take the first one named
    codes = re.findall(r"p([1-9])", prod)
    pi = int(codes[0]) - 1 if codes else None
    raw = last_customer_text(v)
    mixed = bool(raw and REFUSAL_WORDS.search(TODAY_REFUSAL.sub(" ", raw)))
    allp = str(v.get("all_products") or "").strip().lower() in ("yes", "true", "all")
    if raw and ALL_WORDS.search(raw) and not mixed:
        # "both" in "I cannot pay both" is no day for all of them
        allp = True
    cut = is_cut(raw)
    cut_kind = ""
    if cut and kind == "stop" and STOP_WORDS.search(raw):
        pass  # "I do not want to talk" is whole even without a full stop
    elif cut and kind in ("stop", "evasive", "no", "too_much") and not iso and amount is None:
        # half a sentence is no wish to end the call, no non-answer and no refusal yet ("I
        # cannot pay because my" may go on with a day): nothing is counted for now
        cut_kind = kind
        kind = "unclear"
    return {
        "kind": kind,
        "amount": amount,
        "date": iso,
        "date_kind": dk,
        "all": allp,
        "product": pi,
        "mixed": mixed,
        "cut": cut,
        "cut_kind": cut_kind,
        "question": str(v.get("has_question") or "").strip().lower() in ("yes", "true"),
        "said_yes": said_yes,
        "raw": raw,
        "date_text": str(v.get("said_date_text") or ""),
        "amount_text": str(v.get("said_amount_text") or ""),
    }


# An enum variable is cleared to one of its values: the platform rejects '' ("variable
# 'has_question' rejected by schema validation", every turn), so the last turn's value stayed and
# a cut turn got "I do not have that information." again and again (talkative A PUB-05).
REPLY_CLEAR = dict(reply_kind="unclear", all_products="no", about_product="none", has_question="no")


def clear_reply(v):
    for k in REPLY_VARS:
        v[k] = REPLY_CLEAR.get(k, "")


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


def pop_note(v, check=False):
    """After one add_note call: move to the next note or report none left. With `check` (the
    tool reports success in note_ok) a failed note is tried once more before moving on."""
    st = load_state(v) or {}
    notes = st.get("notes") or []
    ok = str(v.get("note_ok") or "").strip().lower() == "true"
    v["note_ok"] = ""
    if check and notes and not ok and str(v.get("note_retry") or "") != "1":
        v["note_retry"] = "1"
        v["note_count"] = str(len(notes))
        return
    v["note_retry"] = ""
    if notes:
        gone = notes.pop(0)
        if check and not ok:
            st["failed_notes"] = list(st.get("failed_notes") or []) + [gone]
            v["failed_notes"] = ",".join(str(x[0]) + ":" + str(x[1]) for x in st["failed_notes"])
    st["notes"] = notes
    v["neg_state"] = json.dumps(st)
    v["note_count"] = str(len(notes))
    if notes:
        v["note_product_id"] = notes[0][0]
        v["note_reason"] = notes[0][1]
