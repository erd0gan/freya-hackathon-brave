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


def _neg_lang():
    """The call's language (CFG["lang"], copied into LOCALE by set_locale/prepare_cfg)."""
    return str(LOCALE.get("lang") or "en")


# Lower case with the Turkish letters folded to ASCII (one character for one, so offsets stay):
# the word lists of other languages match it without suffix or dotted/dotless i variants.
# Folded before lowering: 'İ'.lower() is two characters.
_FOLD = str.maketrans("İIıŞşĞğÜüÖöÇçÂâÎîÛû", "iiissgguuooccaaiiuu")


def _fold(text):
    return str(text or "").translate(_FOLD).lower()


def _txt(text):
    """The customer's text as the call language's word lists read it (English: as it is)."""
    return str(text or "") if _neg_lang() == "en" else _fold(text)


def engine_cal(now, cfg):
    """The calendar facts the engine reads: today and attempt 1's day. calendar_facts would
    bring the date table and the callback window into the decide node, which must stay small
    (embed.CODE_HARD_CAP)."""
    dt = parse_now(now)
    if dt is None:
        return dict()
    a1 = attempt1_date(dt.date(), cfg)
    out = dict(today_iso=dt.date().isoformat(), attempt1_date=a1.isoformat() if a1 else "")
    # a plan step dated by another rule word ("next_business_day", "last_business_day_of_
    # month", ...) was offered on attempt 1's day; the product's deadline still caps it
    rules = dict()
    for step in (cfg.get("neg") or dict()).get("attempt_plan") or []:
        w = str(step.get("date") or "") if isinstance(step, dict) else ""
        if w and w not in ("attempt1", "deadline"):
            d = rule_date(dt.date(), w, cfg)
            if d is not None:
                rules[w] = d.isoformat()
    if rules:
        out["rule_dates"] = rules
    return out


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


def _line(neg, key, default="", **kw):
    """The sentence `key`: the brief's words or the language pack's (neg.lines), else the
    English default; named placeholders ({amount}, {date}, {product}, ...) filled in by plain
    replacement, so a stray brace in a brief's sentence never raises."""
    s = str((neg.get("lines") or {}).get(key) or default)
    for k, v in kw.items():
        s = s.replace("{" + k + "}", str(v))
    if s[:1].islower() and _neg_lang() != "en" and "_item" not in key:
        # a sentence that starts with a name ('ihtiyaç kredisi için ...'): capital first
        # letter, the Turkish way for i and ı (English names start lower only mid-sentence)
        up = dict(i="İ", ı="I") if _neg_lang() in ("tr", "az") else dict()
        s = up.get(s[0], s[0].upper()) + s[1:]
    return s


def new_state(pf, neg, now):
    """pf: product_facts output (pK_* keys)."""
    n = int(pf.get("product_count") or 0)
    ps = []
    for k in range(1, n + 1):
        key = "p" + str(k)
        full = _amount_value(pf.get(key + "_past_due"))  # '1,250.00', '£1,250.00' too
        floor = _amount_value(pf.get(key + "_floor")) or full
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
        if pf.get(key + "_band_nearest"):
            ps[-1]["band_nearest"] = True  # days late outside every band: the nearest one's
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


def _key(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _due_days(p, field, today):
    """Days late from a due date (the mapped `due_date` field, else a field named *due* that
    holds a YYYY-MM-DD day): an account that gives a due date and no days-late figure."""
    for k in ([field] if field else []) + [k for k in p if "due" in str(k).lower()]:
        try:
            d = datetime.date.fromisoformat(str(p.get(k) or "")[:10])
        except Exception:
            continue
        return max((today - d).days, 0)
    return None


def account_products(products, now, cfg):
    """get_account's products as the engine needs them, before product_facts:
    - amounts in major units: CFG["amount_scale"] {response field (path): factor}, 0.01 for a
      field in cents (summary I; requests are scaled back by the request hook);
    - a days-late figure from a due date when the account gives none (HA-5);
    - the product type the config knows when the account gives none or writes it otherwise
      (HA-4: one type configured, or a "*" entry; 'CREDIT_CARD' for 'credit_card')."""
    fmap = cfg.get("product_fields") or dict()
    scale = dict()
    for path, f in (cfg.get("amount_scale") or dict()).items():
        leaf = [x for x in re.split(r"[.\[\]/]+", str(path)) if x]
        try:
            if leaf and float(f) > 0:
                scale[leaf[-1]] = float(f)
        except Exception:
            continue
    dl_f = fmap.get("days_late") or "days_late"
    ty_f = fmap.get("type") or "type"
    known = list(cfg.get("product_names") or dict()) + list(cfg.get("floor_field") or dict())
    types = sorted(set(t for t in known if t != "*"))
    dt = parse_now(now)
    out = []
    for p in products:
        q = dict(p)
        for k, f in scale.items():
            v = _amount_value(q[k]) if q.get(k) not in (None, "") else 0.0
            if v:
                q[k] = round(v * f, 2)  # an unreadable value stays as it is
        if q.get(dl_f) in (None, "") and dt is not None:
            d = _due_days(q, fmap.get("due_date"), dt.date())
            if d is not None:
                q[dl_f] = d
        t = str(q.get(ty_f) or "").strip()
        if t not in types:
            same = [x for x in types if t and _key(x) == _key(t)]
            if same:
                q[ty_f] = same[0]
            elif not t and len(types) == 1:
                q[ty_f] = types[0]
            elif not t and "*" in known:
                q[ty_f] = "*"
        out.append(q)
    return out


def _floor_by_rule(p, rule, fmap):
    """CFG["floor_rule"]: {"field": f} (the floor is that field) or {"percent_of": f,
    "percent": n} (n% of that field, to the cent). None when the rule or the value is missing."""
    if not isinstance(rule, dict):
        return None
    f = rule.get("percent_of") or rule.get("field")
    v = p.get(f) if f in p else p.get(fmap.get(f) or "")
    try:
        v = to_float(v)
        if rule.get("percent_of"):
            return _cents(v * float(rule.get("percent")) / 100.0)
        return v
    except Exception:
        return None


def engine_facts(products, now, cfg):
    """product_facts for the engine (the FACTS node): the products made usable first
    (account_products), then what product_facts cannot know filled in:
    - the floor from CFG["floor_rule"] (HA-6), or, for a product of no known type, from the
      one floor field the config names (HA-4);
    - a spoken name for a product without one (HA-4);
    - a product with no band (no days-late figure at all) is kept, not dropped (HA-5): the
      band with the earliest deadline and the fewest attempts, noted as band_nearest."""
    products = account_products(products, now, cfg)
    pf = product_facts(products, now, cfg)
    neg = cfg.get("neg") or dict()
    fmap = cfg.get("product_fields") or dict()
    ff = cfg.get("floor_field") or dict()
    fields = sorted(set(x for x in ff.values() if x))
    dt = parse_now(now)
    bands = [b for b in cfg.get("bands") or [] if isinstance(b, dict)]
    brief = str(pf.get("product_brief") or "").split("\n")
    for i, p in enumerate(products):
        k = "p" + str(i + 1)
        fl = _floor_by_rule(p, cfg.get("floor_rule"), fmap)
        ptype = str(p.get(fmap.get("type") or "type") or "")
        if fl is None and not pf.get(k + "_floor") and ptype not in ff and len(fields) == 1:
            fl = _floor_by_rule(p, dict(field=fields[0]), fmap)
        if fl is not None and fl > 0:
            pf[k + "_floor"] = "%.2f" % fl
        if not str(pf.get(k + "_name") or "").strip() or pf.get(k + "_name") == "*":
            pf[k + "_name"] = _line(neg, "product_default", "account")
            if i < len(brief) and brief[i].lstrip("*").startswith(":"):
                brief[i] = pf[k + "_name"] + brief[i].lstrip("*")
        if not pf.get(k + "_deadline") and dt is not None and bands:
            dls = [(deadline_for(dt.date(), b, cfg, p), b) for b in bands]
            dl, b = min(dls, key=lambda x: x[0])
            att = [int(x.get("attempts")) for x in bands if str(x.get("attempts")).isdigit()]
            pf[k + "_band"] = str(b.get("name") or "")
            pf[k + "_deadline"] = dl.isoformat()
            pf[k + "_attempts"] = str(min(att)) if att else ""
            pf[k + "_band_nearest"] = "yes"
    pf["product_brief"] = "\n".join(brief)
    return pf


def _amount_value(x):
    """A product amount from product_facts as a number ('1.250,00 TL' too), 0.0 when
    unreadable."""
    try:
        return to_float(x or 0)
    except Exception:
        return 0.0


def present_text(st, neg):
    """The exact words that state every late product and ask the first question."""
    lines = []
    for p in st["p"]:
        if str(_days(p)):
            lines.append(
                _line(
                    neg,
                    "present_product",
                    "Your {product} has {amount} past due and is {days} days late.",
                    product=p["name"],
                    amount=_m(p["full"]),
                    days=_days(p),
                )
            )
        else:
            # no days-late figure from the account (a band chosen without it): not "is  days"
            lines.append(
                _line(
                    neg,
                    "present_product_nodays",
                    "Your {product} has {amount} past due.",
                    product=p["name"],
                    amount=_m(p["full"]),
                )
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
    """'the personal loan', but 'your overdraft' stays as it is. English only: another
    language's sentences carry the name bare."""
    n = str(name or "")
    if _neg_lang() != "en":
        return n
    low = n.lower()
    if low.startswith(("the ", "your ", "my ", "a ", "an ")):
        return n
    return "the " + n


# A turn we could not use. Talkative customers' turns often arrive cut off, many in a row; the
# same sentence said again and again annoyed the customer into hanging up, so it rotates.
SORRY = ["Sorry, I did not catch that.", "Sorry, I missed the end of that.", "Take your time."]
CUT_SORRY = "Sorry, I think you were cut off."


def _cut_sorry(neg):
    return _line(neg, "cut_sorry", CUT_SORRY)


def _sorry(st, r, neg):
    n = max(int(st.get("unclear_run") or 1) - 1, 0)
    if r.get("cut") and n % 2 == 1:
        return _cut_sorry(neg)
    return _line(neg, "sorry_" + str(n % len(SORRY) + 1), SORRY[n % len(SORRY)])


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
        return _line(neg, "offer_question", "Can you do that?")
    if len(st.get("p") or []) > 1:
        return _line(neg, "multi_today_question", "Can you pay all of them today?")
    return _line(neg, "today_question", "Can you make this payment today?")


def _rest(neg):
    return _line(neg, "rest_overdue", "The rest stays overdue.")


def _record_failed(neg, ps):
    return _line(
        neg,
        "record_failed",
        "I could not record the promise for {products}.",
        products=", ".join(_the(q["name"]) for q in ps),
    )


def _understand(neg):
    return _line(neg, "understand", "I understand.")


def _cannot_change(neg):
    return _line(
        neg,
        "cannot_change",
        "A recorded promise cannot be changed in this call. You can change it in the mobile app.",
    )


def _rest_after(p, amount, neg):
    """' The rest stays overdue.' after an offer below the full amount; nothing when the
    amount is the full one (a floor equal to the full amount leaves no rest)."""
    return " " + _rest(neg) if amount < p["full"] - 0.005 else ""


def _pay_for(neg, amount, p, date):
    return _line(
        neg,
        "attempt_offer",
        "Could you pay {amount} for {product} on {date}?",
        amount=_m(amount),
        product=_the(p["name"]),
        date=_d(date),
    )


def _pay_on(neg, amount, date):
    return _line(
        neg, "pay_on", "Could you pay {amount} on {date}?", amount=_m(amount), date=_d(date)
    )


def _num(x):
    try:
        return to_float(x)
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
        return j, _cents(amt), d
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
    date = (
        p["deadline"]
        if step.get("date") == "deadline"
        else (cal.get("rule_dates") or dict()).get(step.get("date"), cal.get("attempt1_date", ""))
    )
    if date and p["deadline"] and date > p["deadline"]:
        date = p["deadline"]
    if _was_refused(p, amount, date):
        # A31 (PUB-10: the floor refused as attempt 2 came again after "too much")
        p["offer"] = None
        return None
    p["offer"] = {"amount": amount, "date": date, "kind": "attempt"}
    p["pending_amount"] = None
    return _pay_for(neg, amount, p, date) + _rest_after(p, amount, neg)


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


def _cents(x):
    # half up: round(2.675, 2) is 2.67
    return int(float(x) * 100 + 0.500001) / 100.0


def _agree(p, amount, date, own_deadline=False):
    p["status"] = "agreed"
    p["amount"] = _cents(amount)
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
            lead = _line(neg, "latest_date", "The latest date I can record is {date}.", date=_d(d))
        elif date_iso and why == "not_business_day":
            lead = _line(
                neg, "not_business_day", "{date} is not a business day.", date=_d(date_iso)
            )
        return (
            _line(
                neg,
                "below_floor",
                "I cannot record {amount}. The minimum is {floor}.",
                amount=_m(amount),
                floor=_m(p["floor"]),
            )
            + " "
            + (lead + " " if lead else "")
            + _pay_on(neg, p["floor"], d)
            + _rest_after(p, p["floor"], neg)
        )
    if not date_iso:
        vague = date_kind == "vague"
        if not vague and (p.get("offer") or {}).get("date"):
            date_iso = p["offer"]["date"]
        else:
            # rule 21; then the deadline, so no day question loops
            key = "vague_asked" if vague else "day_asks"
            if int(p.get(key) or 0) < (_pol(neg, "vague_date_asks") if vague else 2):
                p[key] = int(p.get(key) or 0) + 1
                p["pending_amount"] = amount
                if not vague:
                    return _line(
                        neg,
                        "day_ask_amount",
                        "On which day can you pay {amount}?",
                        amount=_m(amount),
                    )
                # "both next week": the exact day that answers this is for all of them too
                st["vague_all"] = len(targets) > 1
                return _line(neg, "vague_ask", "On which exact day will you pay?")
            if vague and (
                (p.get("offer") or {}).get("kind") == "deadline"
                or _was_refused(p, amount, p["deadline"])
            ):
                # SM-9: the day was asked and the deadline offered; vague again is a non-answer
                # (A30), not the same question once more (nor later, A31)
                _refuse_offer(p)
                return _flat_refusal(st, k, neg, cal)
            p["offer"] = {"amount": amount, "date": p["deadline"], "kind": "deadline"}
            return _line(
                neg,
                "deadline_offer",
                "The latest date I can record is {date}. Could you pay {amount} on that day?",
                date=_d(p["deadline"]),
                amount=_m(amount),
            )
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
        elif res.startswith("after_deadline") and (
            _was_refused(q, amt, q["deadline"])
            or (
                (q.get("offer") or {}).get("kind") == "deadline"
                and (q.get("offer") or {}).get("date") == q["deadline"]
                and abs(float((q.get("offer") or {}).get("amount") or 0) - float(amt)) < 0.005
            )
        ):
            # the late day said again to the deadline offer for it is a no to that offer (A30),
            # never the same offer once more (A31): stress R17 on Freya, 11 Oct, offered
            # "900.00 on Thursday 22 October" nine times to "only on the 23rd"
            _refuse_offer(q, amt, q["deadline"])
            q["offer"] = None
            return _flat_refusal(st, j, neg, cal)
        elif res.startswith("after_deadline"):
            line = _line(
                neg,
                "deadline_offer_product",
                "The latest date I can record for {product} is {date}. Could you pay {amount} "
                "on that day?",
                product=_the(q["name"]),
                date=_d(q["deadline"]),
                amount=_m(amt),
            )
            q["offer"] = {"amount": amt, "date": q["deadline"], "kind": "deadline", "say": line}
        elif res.startswith("not_business_day"):
            nb = res.split(":", 1)[1]
            if len(targets) > 1:
                line = _line(
                    neg,
                    "next_business_day_product",
                    "{date} is not a business day. Could you pay {amount} for {product} on the "
                    "next business day, {next_date}?",
                    date=_d(date_iso),
                    amount=_m(amt),
                    product=_the(q["name"]),
                    next_date=_d(nb),
                )
            else:
                line = _line(
                    neg,
                    "next_business_day",
                    "{date} is not a business day. Could you pay {amount} on the next business "
                    "day, {next_date}?",
                    date=_d(date_iso),
                    amount=_m(amt),
                    next_date=_d(nb),
                )
            q["offer"] = {"amount": amt, "date": nb, "kind": "next_business_day", "say": line}
        elif res == "past":
            q["pending_amount"] = amt
            line = _line(
                neg, "date_past", "The date has to be today or later. On which day can you pay?"
            )
        else:
            q["pending_amount"] = amt
            line = _line(neg, "exact_day_ask", "On which exact day can you pay?")
        if line:
            said.append((j, line))
    if said:
        st["i"] = said[0][0]
        return said[0][1]
    return None


def _split_amounts(st, r):
    """{product index: amount} said for several products in one message: '300 to the card and
    200 to the loan' (each part names one product and one amount), or one amount 'for each' of
    the products named (all of them when none is named). {} otherwise."""
    raw = str(r.get("raw") or "")
    if len(st["p"]) < 2 or not raw:
        return {}
    out = {}
    for part in _claim_parts(raw):
        js = _named_in(st, part)
        if len(js) != 1:
            continue
        rest = " " + _txt(part).lower() + " "
        q = st["p"][js[0]]
        for w in [_txt(q.get("name")).lower()] + _name_words(q):
            rest = re.sub(r"\b" + re.escape(w) + r"\b", " ", rest)
        a = parse_amount(rest)
        if a is not None and a > 0:
            out[js[0]] = a
    live = [j for j, q in enumerate(st["p"]) if q["status"] in ("open", "agreed")]
    out = {j: a for j, a in out.items() if j in live}
    if len(out) >= 2:
        return out
    amount = r.get("amount")
    if amount is not None and _re("each").search(_txt(raw)):
        named = [j for j in _named_in(st, raw) if j in live]
        return {j: amount for j in (named if len(named) >= 2 else live)}
    return {}


def _allocation_ask(st, r, neg, phase):
    """An amount for no product while several are open, said as a total or to 'all of them
    today?' ("five hundred altogether"): which part goes to which product is asked, never the
    whole amount put on one product (stress R04 / F-05, 11 Oct)."""
    amount = r.get("amount")
    live = [q for q in st["p"] if q["status"] in ("open", "agreed")]
    if (
        amount is None
        or len(live) < 2
        or r.get("product") is not None
        or _named_in(st, r.get("raw") or "")
        or not (phase == "ask_today" or _re("total").search(_txt(r.get("raw") or "")))
    ):
        return None
    return _line(
        neg,
        "allocation_ask",
        "How would you like to split {amount} between {products}?",
        amount=_m(amount),
        # "and" is English: other languages get a comma list (the line around it is theirs)
        products=(" and " if _neg_lang() == "en" else ", ").join(_the(q["name"]) for q in live),
    )


def _next_open(st):
    for j, q in enumerate(st["p"]):
        if q["status"] == "open":
            return j
    return -1


def _item(neg, q, key="readback_item"):
    """One promise in a read-back or a recorded list: 'the card, 200.00 on Friday ...'."""
    default = "{product}, {amount} on {date}"
    if key == "readback_item_deadline":
        default += ", which is the latest date I can record for it"
    elif key == "ack_item":
        default = "{product} for {amount} on {date}"
    return _line(
        neg, key, default, product=_the(q["name"]), amount=_m(q["amount"]), date=_d(q["date"])
    )


def _readback(st, neg):
    parts = [
        _item(neg, q, "readback_item_deadline" if q.get("own_deadline") else "readback_item")
        for q in st["p"]
        if q["status"] == "agreed"
    ]
    return (
        _line(neg, "readback", "To confirm: {items}.", items="; ".join(parts))
        + " "
        + _line(neg, "confirm_question", "Do you confirm?")
    )


def _ack(st, neg):
    """'I have noted ...' for the products agreed since the last time it was said."""
    just = [q for q in st["p"] if q["status"] == "agreed" and not q.get("acked")]
    if not just:
        return ""
    for q in just:
        q["acked"] = True
    items = ", ".join(_item(neg, q, "ack_item") for q in just)
    return _line(neg, "ack", "I have noted {items}.", items=items) + " "


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
        return _ack(st, neg) + _line(
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
            return _ack(st, neg) + p["offer"]["say"]
        if st.get("hardship"):
            return _hardship_rest(st, neg)
        if p["used"] == 0:
            # SM-3/HA-8: attempt 1 only when the product's band has one (a band with 0
            # attempts ends the product as refused, no persuasion offer)
            say = _flat_refusal(st, j, neg, cal)
        else:
            say = _attempt_offer(st, j, neg, cal) or _flat_refusal(st, j, neg, cal)
        if say:
            st["phase"] = "offer"
            return _ack(st, neg) + say
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
    what = st.get("transfer") or ""
    # notes on a transfer (Northwind 4.8 "also when the call is transferred"): a product still
    # open takes the transfer's code when it is a note code too (add_note's enum is the reasons'
    # values; Northwind has none for a plan or a person), else its own (refused)
    tr = (neg.get("transfer_reasons") or {}).get(what)
    xfer = rs.get(what) or (tr if tr in rs.values() else None)
    if not xfer and _pol(neg, "notes_on_transfer"):
        # "otherwise ... transferred for any other transfer" (stress F-03, 11 Oct: refused)
        xfer = rs.get("transferred")
    notes = []
    for q in st["p"]:
        if q["status"] != "recorded":
            s = sem.get(q["status"], "refused")
            notes.append([q["id"], xfer if xfer and q["status"] == "open" else rs.get(s, s)])
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


def _transfer(st, what, neg, open_as="open"):
    st["transfer"] = what
    st["transfer_queue"] = (neg.get("queues") or {}).get(what, what)
    # Brief B rule 10: agreed but unrecorded promises are dropped. A hardship transfer: every
    # product still open is a hardship one (its note, when notes are written on a transfer).
    for q in st["p"]:
        if q["status"] in ("agreed", "open"):
            q["status"] = open_as
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


# Words of a product name that pick out no product (English, then Turkish folded to ASCII).
NAME_SKIP = ("ending", "account", "the", "your", "and", "ile", "biten", "sonu", "hesap", "hesabi")


def _name_words(q):
    return [
        w for w in re.findall(r"[a-z]{3,}|\d{4}", _txt(q.get("name")).lower()) if w not in NAME_SKIP
    ]


def _named_in(st, text):
    """Products named: whole name, then a word only it has, then a shared word. Outside
    English a name word may carry a suffix ('kartı', 'kartımı', 'krediyi' for 'kredi kartı')."""
    t = " " + _txt(text).lower() + " "
    found = []
    for i, q in enumerate(st["p"]):
        nm = _txt(q.get("name")).lower().strip()
        if nm and nm in t:
            found.append(i)
            t = t.replace(nm, " ")
    words = [_name_words(q) for q in st["p"]]
    end = r"\b" if _neg_lang() == "en" else ""

    def said(ws):
        return any(re.search(r"\b" + w + end, t) for w in ws)

    for i, ws in enumerate(words):
        own = [w for w in ws if not any(w in o for n, o in enumerate(words) if n != i)]
        if i not in found and said(own):
            found.append(i)
    for i, ws in enumerate(words):
        if i not in found and said(ws):
            found.append(i)
    return sorted(found)


def _claim_parts(raw):
    t = _txt(str(raw or "").replace("’", "'"))
    return [x for x in re.split(r"[,;.!?]+|\b(?:and|but|though|although|while)\b", t) if x]


def _claims(st, raw):
    """{product: 'paid' | 'dispute'} said about one named product in a message that may say
    other things ("No, I paid the credit card yesterday, and the personal loan isn't mine. As
    for the car loan, I won't pay": stress D-06 on Freya, 11 Oct, the card and the loan got
    persuasion offers). Only a part of the message that names exactly one open product."""
    out = {}
    for part in _claim_parts(raw):
        if _re("dispute_claim").search(part):
            c = "dispute"
        elif _re("paid_claim").search(part) and not _re("not_paid").search(part):
            c = "paid"
        else:
            continue
        named = _one_named(st, part)
        if named is not None and st["p"][named]["status"] in ("open", "agreed"):
            out.setdefault(named, c)
    return out


def _one_named(st, text):
    """The one product a part of a message names: by a word only it has ('personal' in 'the
    personal loan isn't mine', though 'loan' is in two names), else by all it names."""
    t = " " + _txt(text).lower() + " "
    words = [_name_words(q) for q in st["p"]]
    end = r"\b" if _neg_lang() == "en" else ""
    own = [
        i
        for i, ws in enumerate(words)
        if any(
            re.search(r"\b" + w + end, t)
            for w in ws
            if not any(w in o for n, o in enumerate(words) if n != i)
        )
    ]
    named = own or _named_in(st, text)
    return named[0] if len(named) == 1 else None


def _claim_rest(raw):
    """The parts of a message that claim nothing (where a refusal or an offer may be)."""
    return " ".join(
        x
        for x in _claim_parts(raw)
        if not _re("dispute_claim").search(x) and not _re("paid_claim").search(x)
    )


def _yes_part(raw):
    """The words before a refusal ("Yes for the card, but I won't pay the loan")."""
    raw = _txt(raw)
    masked = _re("today_refusal").sub(lambda m: " " * len(m.group(0)), raw)
    m = _re("refusal").search(masked)
    return raw[: m.start()] if m else raw


def _yes_products(st, r, among=("open",)):
    """The products (status in `among`) a yes is for, or None for all. With a refusal in the
    message, the ones named before it (review: names inside the refusal agreed all three)."""
    n = len(st["p"])
    j = r.get("product")
    j = j if j is not None and 0 <= j < n and st["p"][j]["status"] in among else None
    raw = _txt(r.get("raw")).lower()
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


def _restates_readback(st, r):
    """"Yes, the home premium on the sixteenth and the motor premium on the twentieth": a yes
    to a read-back of several promises that says their own days and amounts again, each one
    agreed (stress G-06 on Freya, 11 Oct: taken as one new day for both, the read-back flipped
    five times). A no, a 'but' or a number nobody agreed is a change."""
    raw = _txt(r.get("raw"))
    ag = [q for q in st["p"] if q["status"] == "agreed"]
    if len(ag) < 2 or _neg_lang() != "en" or not re.match(r"\W*(?:yes|yeah|yep|correct)\b", raw, re.I):
        return False
    if _re("refusal").search(raw) or re.search(r"\b(?:but|instead|change|rather)\b", raw, re.I):
        return False
    days = {str(q.get("date") or "") for q in ag}
    amts = {round(float(q["amount"]), 2) for q in ag}
    for n in re.findall(r"\d+(?:\.\d+)?", raw):
        if round(float(n), 2) not in amts and not any(d[8:] == "%02d" % float(n) for d in days):
            return False
    return (not r.get("date") or r["date"] in days) and (
        r.get("amount") is None or round(float(r["amount"]), 2) in amts
    )


def _yes_to_offered_day(p, r):
    """A yes naming only the weekday of the day on the table (the offer, or the agreed day at
    the read-back) means that day, not the nearest one (A PUB-07). True: a plain yes to it."""
    o = p.get("offer") or {}
    day = o.get("date") or (p.get("date") if p.get("status") == "agreed" else None)
    if not (r.get("said_yes") and day and r.get("date")):
        return False
    if r["date"] != day:
        text = _txt(r.get("date_text"))
        if _re("day_number").search(text):
            return False
        if _re("move").search(text):
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
# Words that only multiply the part before them ("three" ... "hundred").
MULTIPLIERS = ("hundred", "thousand", "grand")


def _joiners():
    return JOINERS if _neg_lang() == "en" else tuple(_words("joiners").split())


def _multipliers():
    return MULTIPLIERS if _neg_lang() == "en" else tuple(_words("multipliers").split())


def _amount_unfinished(text, raw):
    """Ends in a joiner, or the turn was cut right after it."""
    words = re.findall(r"[a-z0-9]+", _txt(text).lower())
    if words and words[-1] in _joiners():
        return True
    raw = _txt(raw).strip().lower()
    if not raw or not is_cut(raw):
        return False
    rw = re.findall(r"[a-z0-9]+", raw)
    if rw and rw[-1] in _joiners():
        rw = rw[:-1]
    return bool(words and rw and rw[-1] == words[-1])


def _join_amount(part, text, lo=None, hi=None):
    """The amount of a part and its rest. A smaller rest adds up ("four hundred" "fifty"); a
    rest that is only a multiplier multiplies ("three" "hundred"). A rest as big as the part
    after a joiner is the amount said again. Without a joiner ("4" "50"), when the rest alone
    is below the floor `lo` and the two together lie in [lo, hi], it is the two together;
    when both readings lie in [lo, hi] ("1" "250"): "ask", the customer is asked again."""
    a = parse_amount(part)
    b = parse_amount(text)
    if a is None or b is None:
        return b if b is not None else a
    rest = re.findall(r"[a-z]+|\d+", _txt(text).lower())
    if rest and all(w in _multipliers() for w in rest):
        both = parse_amount(part + " " + text)
        return both if both is not None else b
    if b < a:
        both = parse_amount(part + " " + text)
        if both is not None and a < both < 2 * a and abs(both * 100 - round(both * 100)) < 1e-6:
            return both
        return round(a + b, 2)
    pw = re.findall(r"[a-z]+|\d+", _txt(part).lower())
    if lo is None or hi is None or (pw and pw[-1] in _joiners()):
        return b
    p, t = str(part).strip(), str(text).strip().rstrip(".")
    both = float(p + t) if p.isdigit() and t.isdigit() else parse_amount(p + " " + t, lo, hi)
    if both is None or abs(both - b) < 0.005 or not lo - 0.005 <= both <= hi + 0.005:
        return b
    return both if b < lo - 0.005 else ("ask" if b <= hi + 0.005 else b)


def _pieces(st, r, kind, neg):
    """Joins an amount said over two turns ("six hundred and" / "thirty-seven fifty").
    Returns the kind, or None to wait for the rest."""
    part = str(st.get("amount_part") or "")
    text = str(r.get("amount_text") or "").strip()
    if part:
        if text:
            ps = st.get("p") or []
            q = ps[st.get("i", 0)] if 0 <= st.get("i", 0) < len(ps) else {}
            a = _join_amount(part, text, q.get("floor"), q.get("full"))
            if a == "ask":
                # two readings both possible ("1" ... "250"): asked again, never guessed
                r["amount"], r["amount_bad"], text = None, True, ""
                r["date"] = r.get("date") or st.get("part_date") or None
                kind = "offer" if kind in ("yes", "no", "too_much", "evasive", "unclear") else kind
            elif a is not None:
                r["amount"] = a
                # still unfinished: the joined number keeps a joiner at its end
                more = _amount_unfinished(text, "") and _joiners()
                text = str(a) + (" " + more[0] if more else "")
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
        st["say"] = _cut_sorry(neg) + " " + _line(neg, "go_on", "Please go on.")
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
    kind = _pieces(st, r, kind, neg)
    if kind is None:
        return st  # half an amount: wait for the rest
    if r.get("also_hardship") and kind in ("restructure", "no", "too_much", "evasive", "stop"):
        # SM-1: hardship comes before a plan request, a refusal or a stop said with it (A32
        # "comes before rules 30 and 41"); the model flags it next to the reply's one label
        kind = "hardship"
    if (
        (r.get("also_hardship") or r.get("hardship_words"))
        and kind in ("offer", "vague", "yes", "no", "too_much", "evasive", "unclear")
        and _pol(neg, "hardship") == "transfer"
        and not st.get("hardship")
    ):
        # a brief that transfers on hardship "even if a voluntary amount appears in the same
        # turn" (stress F-02 on Freya, 11 Oct: "I lost my job yesterday, but I could pay three
        # hundred on the card today" was taken as an offer; persuasion went on)
        kind = "hardship"
    st["unclear_run"] = int(st.get("unclear_run") or 0) + 1 if kind == "unclear" else 0
    # whole replies that answer nothing ("Hmm."), not cut off and no question: after three in a
    # row the next one is a non-answer (A30: uses an attempt), so the call cannot go on forever
    if kind != "unclear" or r.get("question"):
        st["blank_run"] = 0
    elif not r.get("cut"):
        st["blank_run"] = int(st.get("blank_run") or 0) + 1
        if st["blank_run"] > 3:
            st["blank_run"] = 0
            kind = "no" if st["phase"] == "confirm" else "evasive"
    if (
        kind in ("no", "evasive", "unclear", "too_much")
        and st["phase"] in ("ask_today", "offer", "confirm")
        and _split_amounts(st, r)
    ):
        # "No, it's 300 to the card and 200 to the loan": amounts per product are an offer
        kind = "offer"
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
    if kind in ("offer", "vague", "too_much") and pk["status"] in ("recorded", "failed"):
        # SM-7/A36: a promise the bank holds is never reopened (a hardship line after the
        # recording led to a second create_promise and a false "recorded" sentence); one the
        # bank refused twice is not tried a third time (A37)
        if pk["status"] == "recorded":
            st["say"] = _cannot_change(neg) + " " + st["last_ask"]
        else:
            st["say"] = _record_failed(neg, [pk]) + " " + st["last_ask"]
        return st
    if r.get("amount_is"):
        r["amount"] = pk[r["amount_is"]]  # 'the minimum' / 'all of it'
    ad = st.pop("amount_date", "")
    if r.get("amount") is not None and not r.get("date") and ad:
        r["date"] = ad  # said with the amount asked again
    if r.get("amount") is not None and r["amount"] < pk["floor"] - 0.005 and r.get("amount_text"):
        # "three fifty" for a 420 debt with a 126 floor means 350, not 3.50
        alt = parse_amount(r["amount_text"], pk["floor"], pk["full"])
        if alt is not None:
            r["amount"] = alt
    if _yes_to_offered_day(st["p"][k], r) and kind == "offer" and phase in ("offer", "confirm"):
        kind = "yes"  # "Yes, that Friday" names the day on the table
    elif kind == "offer" and r.get("said_yes") and phase in ("offer", "confirm"):
        # SM-4: "Yes, 420 on Friday" restates the amount and day on the table: it is the yes,
        # not a new offer (a read-back said again and again). Several promises read back:
        # only for a product the reply picks out.
        o = pk.get("offer") or {}
        agreed = pk.get("status") == "agreed"
        day = o.get("date") or (pk.get("date") if agreed else None)
        amt = o.get("amount") if o else (pk.get("amount") if agreed else None)
        one = phase == "offer" or len([q for q in st["p"] if q["status"] == "agreed"]) == 1
        if (
            day
            and amt is not None
            and one
            and (not r.get("date") or r["date"] == day)
            and (r.get("amount") is None or abs(float(r["amount"]) - float(amt)) < 0.005)
        ):
            kind = "yes"
    if kind == "offer" and phase == "confirm" and _restates_readback(st, r):
        kind = "yes"
    if kind == "restructure" and str(neg.get("restructure") or "transfer") == "no":
        # HA-10 (optional knob, not a policy key the spec must set): a brief with no plan
        # transfer; a plan request is then a no to the payment asked (A30)
        kind = "no"
    if kind == "restructure":
        _transfer(st, "restructuring", neg)
        return st
    help_line = _line(neg, "help_first", "Before I transfer you, can I help you with this myself?")
    if st.get("help_open"):
        # an answer to help_first, not to the question before it
        if kind == "unclear":
            st["say"] = _sorry(st, r, neg) + " " + help_line
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
            _transfer(st, _pol(neg, "hardship_queue"), neg, "hardship")
            return st
        if kind in ("offer", "vague") or named:
            phase = "offer"  # A32: a date or an amount the customer names is still recorded
            kind = "offer" if named else kind
        elif kind in ("paid", "dispute"):
            phase = "offer"
        elif kind == "unclear":
            # no answer: the offer stays open, uncounted, re-asked short (A PUB-05)
            st["say"] = _sorry(st, r, neg) + " " + _short(st, neg)
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
            _transfer(st, _pol(neg, "hardship_queue"), neg, "hardship")
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
            # SM-8: what is open now is the support offer, not the payment asked before it
            st["last_ask"] = support_offer
            return st
    if kind == "stop":
        st["stop"] += 1
        if st["stop"] >= _pol(neg, "stop_requests_to_close"):
            for q in st["p"]:
                if q["status"] in ("open", "agreed"):
                    q["status"] = "refused"
            _finish(st, neg)
            return st
        st["say"] = _understand(neg) + " " + _short(st, neg)
        return st
    claims = (
        _claims(st, r.get("raw"))
        if len(st["p"]) > 1
        and phase in ("ask_today", "offer")
        and kind in ("no", "paid", "dispute", "offer", "vague", "too_much", "evasive", "yes")
        else {}
    )
    if claims:
        # paid / not mine said about a named product next to other answers: that product is
        # settled with no persuasion offer; the rest of the message is about the others
        lines = []
        for j, c in claims.items():
            q = st["p"][j]
            q["status"], q["offer"], q["pending_amount"] = c, None, None
            line = (
                _line(neg, "paid", "Payments can take one business day to appear.")
                if c == "paid"
                else _line(neg, "dispute_noted", "I have noted that.")
            )
            if line not in lines:
                lines.append(line)
        pre = " ".join(lines)
        left = [j for j, q in enumerate(st["p"]) if q["status"] in ("open", "agreed")]
        rest = _claim_rest(r.get("raw"))
        one = _one_named(st, rest)
        others = [one] if one in left else [j for j in _named_in(st, rest) if j in left]
        if kind in ("paid", "dispute") and others and _re("refusal").search(rest):
            kind = "no"  # "..., and I won't pay the car loan"
        if kind in ("paid", "dispute") or not left:
            say = _advance(st, neg, cal) if left else None
            if say is None:
                if "paid" in claims.values():
                    st["final_lines"].append(
                        _line(neg, "paid", "Payments can take one business day to appear.")
                    )
                _finish(st, neg)
                return st
            st["say"] = pre + " " + say
            st["last_ask"] = say
            return st
        if k not in left:
            k = others[0] if others else left[0]
            st["i"] = k
        prefix = (pre + " " + prefix).strip()
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
        if r.get("all") and not r.get("mixed") and phase != "closing":
            # SM-5: "I already paid all of them" / "none of them is mine" is about every
            # product still open, not the first one only
            for q in st["p"]:
                if q["status"] in ("open", "agreed"):
                    q["status"] = kind
                    q["offer"] = None
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
        st["q_run"] = int(st.get("q_run") or 0) + 1
        if st["q_run"] > 5 and phase in ("ask_today", "offer"):
            # SM-10: only questions, five in a row: the next one is a non-answer (A30)
            st["q_run"] = 0
            kind = "evasive"
        else:
            st["mode"] = "llm"
            st["say"] = (
                "Answer the customer's last question in one short sentence, only with what you "
                "know from this call; if you do not have the information, say so. Then say "
                'exactly: "' + st["last_ask"] + '"'
            )
            return st
    else:
        st["q_run"] = 0
    if phase == "closing":
        if kind in ("offer", "vague", "too_much"):
            # A36: a recorded promise cannot be changed in this call; with none recorded and
            # one the bank refused, never "recorded" (stress D-05 on Freya, 11 Oct)
            failed = [q for q in st["p"] if q["status"] == "failed"]
            if failed and not any(q["status"] == "recorded" for q in st["p"]):
                st["say"] = _record_failed(neg, failed) + " " + st["last_ask"]
            else:
                st["say"] = _cannot_change(neg) + " " + st["last_ask"]
            return st
        if kind == "unclear" and r.get("cut") and not st.get("closing_reasked"):
            # one cut-off turn gets one more ask; a whole one ends the call
            st["closing_reasked"] = True
            st["say"] = _sorry(st, r, neg) + " " + st["last_ask"]
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
        if (
            kind == "yes"
            and agreed
            and (_neg_lang() == "en" or "consent" in (LANG_WORDS.get(_neg_lang()) or {}))
            and not r.get("consent_words")
            and (r.get("question") or r.get("hear_only"))
        ):
            # "Yes, I can hear you. Does that take money now?" is no consent (stress R03): the
            # question is answered (answer_first) and the read-back is asked again; nothing is
            # written
            st["say"] = st["last_ask"] = _readback(st, neg)
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
                say = _pay_for(neg, amt, q, d) + _rest_after(q, amt, neg)
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
                st["say"] = _understand(neg) + " " + _readback(st, neg)
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
            st["say"] = _sorry(st, r, neg) + " " + _short(st, neg)
            return st
        else:
            st["say"] = _readback(st, neg)
            return st
    # ask_today or offer
    p = st["p"][k]
    multi = len(st["p"]) > 1
    if (
        r.get("amount_bad")
        and kind in ("offer", "vague")
        and not (multi and _split_amounts(st, r))
    ):
        # asked again, never defaulted; the third time a non-answer (A30). Amounts said per
        # product ('300 to the card and 200 to the loan') are read below.
        st["amount_asks"] = int(st.get("amount_asks") or 0) + 1
        if st["amount_asks"] > 2:
            kind = "evasive"
        else:
            st["amount_date"] = r.get("date") or ""
            # brief rule: never ask "how much can you pay?"; ask which amount they meant
            st["say"] = st["last_ask"] = _line(
                neg, "amount_ask", "Sorry, I did not catch the amount. Which amount do you mean?"
            )
            return st
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
        if r.get("full_too_much") and amount is not None and amount < p["full"] - 0.005:
            # "I can't pay the full 480, I have 120": the full amount was said to be too much,
            # so attempt 1 names the floor (A30; stress R01 on Freya, 11 Oct)
            p["too_much"] = True
        split = _split_amounts(st, r) if multi else {}
        alloc = None if split or not multi else _allocation_ask(st, r, neg, phase)
        if split:
            # each amount to its own product, each checked against its own floor
            say = None
            for j in sorted(split):
                s1 = _apply_offer(
                    st, j, split[j], r.get("date"), r.get("date_kind"), neg, cal, now, cfg, False
                )
                say = say or s1
        elif alloc:
            say = alloc
        else:
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
                _line(
                    neg,
                    "floor_for",
                    "The minimum for {product} is {amount}.",
                    product=_the(p["name"]),
                    amount=_m(p["floor"]),
                )
                + " "
                + _pay_on(neg, p["floor"], d)
                + " "
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
        st["say"] = _sorry(st, r, neg) + " " + _short(st, neg)
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
            _line(
                neg,
                "promise_recorded",
                "Your promise is recorded: {items}.",
                items="; ".join(_item(neg, q) for q in rec),
            )
            + " "
            + _line(neg, "recorded", "You will receive a text message confirming this promise.")
        )
    if bad:
        parts.append(_record_failed(neg, bad))
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
    "said_date_iso",
    "all_products",
    "about_product",
    "has_question",
    "also_hardship",
)


# "Would that work?" after an offer: the read-back answers it (stress R11 on Freya, 11 Oct: "I
# am sorry, I do not have that information. To confirm: ...").
OFFER_CHECK = re.compile(
    r"\b(?:would|will|does|do|is|can|could)\s+(?:that|this|it)\s+(?:\w+\s+)?"
    r"(?:work|ok|okay|alright|all right|fine|possible|acceptable|enough|do)\b|"
    r"\bcan i do that\b",
    re.I,
)


def _only_offer_check(raw, phase):
    """Every question in the message only asks whether the customer's own offer is fine, and
    the engine's answer is the read-back of it."""
    if phase != "confirm" or _neg_lang() != "en":
        return False
    qs = re.findall(r"[^.!?]*\?", str(raw or ""))
    return bool(qs) and all(OFFER_CHECK.search(q) for q in qs)


def answer_first(st, r):
    """The customer also asked something (a robot, a discount, ...): the model answers it in
    one sentence, then says the engine's exact sentence."""
    if (
        r.get("question")
        and r.get("kind") != "question"
        and st.get("action") == "say"
        and st.get("mode", "speak") == "speak"
        and st.get("say")
        and not _only_offer_check(r.get("raw"), st.get("phase"))
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
# A refusal is about a part of the debt only with words that pick a part out (or a product's
# name): "Work is so busy I can't even think. Yes." is a yes to all of them.
PART_WORDS = re.compile(
    r"\b(except|apart from|but not|not the|not for the|only|just the|the other|one of|"
    r"the (first|second|third|last) one|either|neither|none of|the rest)\b",
    re.I,
)


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
# Words of a number or a share in an amount text: one we cannot read is asked again.
AMOUNT_WORDS = re.compile(
    r"\d|\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|thirty|"
    r"forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|grand|half|quarter|third|"
    r"percent|minimum|lowest|least|full|whole)\b",
    re.I,
)
# The whole amount text names the floor (group 1) or the full amount (group 2).
AMOUNT_IS = re.compile(
    r"(?:just |only )?(?:(?:the |that |this |your )?(minimum|lowest|least)"
    r"(?: payment| amount| i can(?: pay)?)?"
    r"|(all of it|everything|in full|all|(?:the |that |this |your )?(?:full|whole)"
    r"(?: amount| thing| lot)?))",
    re.I,
)
# A product already paid / not the customer's, said in one part of a message (_claims).
PAID_CLAIM = re.compile(r"\b(?:paid|settled|cleared)\b", re.I)
NOT_PAID = re.compile(
    r"(?:n't|\bnot|\bnever|\bwill|'ll|\bgoing to|\bgonna|\bcan|\bcould|\bwould|\bshould|"
    r"\bto be|\bget it|\bhave it)\s+(?:\w+\s+){0,5}?(?:paid|settled|cleared)\b",
    re.I,
)
DISPUTE_CLAIM = re.compile(
    r"\bisn't mine\b|\bnot mine\b|\bnot my (?:\w+ )?(?:debt|account|card|loan|policy)\b|"
    r"\bnever (?:had|opened|took out|owned)\b|\bdo(?:n't| not) recogni[sz]e\b|"
    r"\bclosed (?:\w+ )?(?:years|long|ages|months) ago\b|\bwas closed\b|"
    r"\bpaid off (?:\w+ )?(?:years|long|ages|months) ago\b",
    re.I,
)
# Saying the full amount is too much, even in a message that also names a smaller amount.
FULL_TOO_MUCH = re.compile(
    r"\btoo much\b|\b(?:can'?t|cannot|can not|couldn'?t|unable to|not able to)\s+"
    r"(?:pay|afford|manage|do)\s+(?:(?:the|that|this|it)\s+)?"
    r"(?:full|whole|entire|all\b|everything|in full)",
    re.I,
)
# Words that agree to a read-back, and a yes that only says the line works ("Yes, I can hear
# you"): stress R03 on Freya, 11 Oct, "Yes, I can hear you. Does that take money now?" was
# taken as the confirmation and the promise was written without consent.
CONSENT_WORDS = re.compile(
    r"\b(confirm\w*|agree\w*|correct|right|go ahead|fine|do it|record it|sounds good|"
    r"that works|deal|ok|okay|sure|absolutely|definitely|of course|yes please)\b",
    re.I,
)
HEAR_ONLY = re.compile(
    r"\b(?:can|could) hear you\b|\bhear you (?:fine|ok|okay|well|clearly|now)\b|"
    r"\bi'?m (?:still )?(?:here|listening)\b|\bi (?:understand|see)\b",
    re.I,
)
# The customer's own hardship in so many words (illness, a death, losing the job).
HARDSHIP_WORDS = re.compile(
    r"\b(?:lost (?:my|our) (?:job|work|income)|been (?:made redundant|laid off|let go)|"
    r"(?:i'?m|i am|i've been|i have been) (?:unemployed|out of work|seriously ill|in hospital)|"
    r"(?:my|our) (?:mother|father|mum|mom|dad|husband|wife|partner|son|daughter|brother|"
    r"sister|child|grand\w+|uncle|aunt) (?:has |just )?(?:passed away|died)|bereavement)\b",
    re.I,
)
# One amount for every product ("three hundred to each"), and an amount that is a total for
# several products ("five hundred altogether"): stress F-05 / R04 on Freya, 11 Oct.
EACH_WORDS = re.compile(r"\b(each|every one|apiece|per (?:product|account|one)|for every)\b", re.I)
TOTAL_WORDS = re.compile(
    r"\b(altogether|all together|in total|total|combined|across|between|split|overall)\b", re.I
)
# A day text that points at the day on the table ('that day', 'then'), not a new day.
SAME_DAY =re.compile(r"(?:on |by )?(?:that|then|the same|same|it)(?: day| date| one)?", re.I)
END_MARK = re.compile(r"[.!?…\"')\]]\s*$")


def is_cut(raw):
    """The simulator often sends a customer turn cut off mid-sentence (no end mark), up to 13
    in a row with a talkative customer; a turn with an end mark is whole."""
    t = str(raw or "").strip()
    return bool(t) and not END_MARK.search(t)


# The same word lists for a call in another language (CFG["lang"]), matched on _fold(text):
# lower case ASCII, so a suffix needs no variant ('ödeyemem', 'ödemeyeceğim' -> 'odeyemem',
# 'odemeyecegim'). General language only, never a bank's words. A language without lists here
# leaves these decisions to the model's labels (see decline_real, _amount_words).
# A negated verb: 'ödemem', 'ödeyemem', 'ödemiyorum', 'ödemeyeceğim' ('tamam' is no negation).
_TR_NEG = r"(?!tamam\b)\w+(?:(?:mem|mam|meyiz|mayiz)\b|(?:miyor|muyor|meyece|mayaca)\w*)"
LANG_WORDS = {
    "tr": {
        "all": r"\b(?:hepsi\w*|her ikisi\w*|ikisi(?:ni|ne)? de|ucu(?:nu|ne)? de)\b",
        "today_refusal": r"\bbugun\w*(?:\s+\w+){0,3}?\s+(?:"
        + _TR_NEG
        + r"|olmaz|degil\w*|yok|mumkun degil)",
        "refusal": r"\b(?:hayir|degil\w*|olmaz|yok|asla|istemiyorum)\b|\b" + _TR_NEG,
        "part": r"\b(?:haric|disinda|sadece|yalnizca|yalniz|diger\w*|biri|birini|hicbir\w*|"
        r"geri kalan\w*|ilki\w*|ikincisi\w*|ucuncusu\w*|sonuncusu\w*)\b",
        "stop": r"\b(?:konusmak|gorusmek|devam etmek) istemiyorum|\baramayin\w*|\bbeni arama\w*|"
        r"\brahat birak\w*|\bilgilenmiyorum|\btelefonu kapat\w*|\bkapatiyorum|\bkapatacagim|"
        r"\bkapat\b|\bhosca kal\w*|\bgorusuruz\b",
        "refuse_answer": r"\b(?:soyle|ver|paylas|cevapla|yanitla|cevap ver)\w*?"
        r"(?:(?:mem|mam)\b|(?:miyor|meyece|mayaca)\w*)|"
        r"\b(?:soylemek|vermek|paylasmak|cevaplamak|yanitlamak|cevap vermek) istemiyorum",
        "about": r"\bne hakkinda\b|\b(?:neden|niye|ne icin|nereden)\s+(?:\w+\s+)?"
        r"(?:ar|soru|iste|isti|gerek|lazim)\w*|\bkimsiniz\b|\bkim(?:sin| bu)\b|\bbu ne\b",
        "amount_is": r"(?:sadece |yalnizca )?(?:(?:o |bu )?(asgari\w*|minimum\w*|en az\w*|"
        r"en dusuk\w*)(?: (?:tutar|odeme|miktar)\w*)?|((?:borcun |borcumun |o |bu )?"
        r"(?:tamami\w*|hepsi\w*|tumu\w*|(?:tam|toplam|butun|tum)(?: (?:tutar|borc|miktar)\w*)?)))",
        "full_too_much": r"\bcok fazla\b|\bfazla gel\w*|\b(?:tamami\w*|hepsi\w*|tumu\w*|butun\w*)"
        r"\s+(?:\w+\s+)?" + _TR_NEG,
        "same_day": r"(?:o|ayni|bu)(?: gun\w*| tarih\w*| zaman)?",
        "consent": r"\b(?:onay\w*|kabul\w*|tamam\w*|dogru\w*|olur|peki|anlastik)\b",
        "hear_only": r"\b(?:sizi )?duyuyorum\b|\bdinliyorum\b|\bburadayim\b",
        "each": r"\b(?:her biri\w*|her birine|her ucune|her ikisine|ayri ayri)\b",
        "total": r"\b(?:toplam\w*|hepsi icin|birlikte|bolus\w*|paylastir\w*)\b",
        "hardship": r"\b(?:isimi kaybettim|issiz kaldim|isten cikarildim|hastaneye? \w*|"
        r"vefat \w*|hayatini kaybetti)\b",
        "day_number": r"\d|\b(?:bir|iki|uc|dort|bes|alti|yedi|sekiz|dokuz|on|yirmi|otuz)",
        "move": r"\b(?:haftaya|gelecek|onumuzdeki|sonraki|ertesi|obur|hafta)\w*",
        "joiners": "ve virgul nokta arti",
        "multipliers": "yuz bin milyon",
    },
}
NEVER = re.compile(r"(?!)")
EN_WORDS = dict(
    all=ALL_WORDS,
    today_refusal=TODAY_REFUSAL,
    refusal=REFUSAL_WORDS,
    part=PART_WORDS,
    stop=STOP_WORDS,
    refuse_answer=REFUSE_ANSWER,
    about=ABOUT_WORDS,
    amount_words=AMOUNT_WORDS,
    amount_is=AMOUNT_IS,
    consent=CONSENT_WORDS,
    hear_only=HEAR_ONLY,
    each=EACH_WORDS,
    total=TOTAL_WORDS,
    hardship=HARDSHIP_WORDS,
    full_too_much=FULL_TOO_MUCH,
    paid_claim=PAID_CLAIM,
    not_paid=NOT_PAID,
    dispute_claim=DISPUTE_CLAIM,
    same_day=SAME_DAY,
    day_number=DAY_NUMBER,
    move=MOVE_WORDS,
)


def _words(name):
    return str((LANG_WORDS.get(_neg_lang()) or {}).get(name) or "")


def _re(name):
    """The call language's pattern for `name` (English: the module's own; a language with no
    list: one that matches nothing)."""
    if _neg_lang() == "en":
        return EN_WORDS[name]
    w = _words(name)
    return re.compile(w) if w else NEVER


def _amount_words(text):
    """An amount text that names a number or a share (speech_lib.amount_like knows the
    Turkish words). In a language nobody reads here, any words at all: an amount that cannot
    be read is asked again, never taken as the full amount."""
    text = str(text or "")
    if _neg_lang() == "en":
        return bool(AMOUNT_WORDS.search(text))
    if _neg_lang() not in LANG_WORDS:
        return bool(text.strip())
    return bool(amount_like(text))


def _said_amount(v):
    """said_amount (the amount the model wrote in digits, contract §5) when it is a plain
    positive number, else None."""
    s = str(v.get("said_amount") or "").strip()
    if not re.fullmatch(r"\d{1,9}(?:[.,]\d{1,2})?", s):
        return None
    x = float(s.replace(",", "."))
    return x if x > 0 else None


def _said_date_iso(v, today):
    """said_date_iso (the day the model wrote as YYYY-MM-DD, contract §5) when it is a real
    day from today on, within a year; a day in the past or years ahead is a slip."""
    try:
        d = datetime.date.fromisoformat(str(v.get("said_date_iso") or "").strip()[:10])
    except Exception:
        return ""
    if today is not None and not today <= d <= today + datetime.timedelta(days=400):
        return ""
    return d.isoformat()


def decline_real(raw):
    """A security answer the model labelled `declines` counts when the customer's own words
    refuse, end the talk or ask what the call is about, or are a whole short answer ("No.",
    "Nope.", "Hayır." - SM-2). Half a sentence or long small talk stays unclear (research
    T1). In a language without word lists the label is trusted for a whole sentence."""
    raw = str(raw or "")
    if not raw.strip():
        return True
    t = _txt(raw)
    if _re("stop").search(t) or _re("refuse_answer").search(t):
        return True
    if is_cut(raw):
        return False
    if _re("refusal").search(t) or _re("about").search(t):
        return True
    return len(raw.split()) <= 8 or (_neg_lang() != "en" and _neg_lang() not in LANG_WORDS)


def read_reply(v, today):
    kind = str(v.get("reply_kind") or "").strip().lower()
    if kind not in SEMANTIC_KINDS:
        kind = "unclear"
    date_text = str(v.get("said_date_text") or "")
    iso, dk = parse_spoken_date(date_text, today)
    dt = re.sub(r"[\s.,!?]+", " ", _txt(date_text).lower()).strip()
    same = bool(dt) and bool(_re("same_day").fullmatch(dt))
    offer = kind in ("offer", "yes", "vague")
    if not iso and dk == "none" and not same and (dt or kind in ("offer", "vague")):
        # nothing our parser reads: the day the model wrote as YYYY-MM-DD (contract §5),
        # checked like any day (the date rules come later, at the offer)
        iso = _said_date_iso(v, today)
        dk = "date" if iso else dk
    if not iso and dt and not same:
        dk = "vague"  # 'this weekend': asked once, then the deadline
    amount_text = str(v.get("said_amount_text") or "")
    amount = parse_amount(amount_text)
    m = _re("amount_is").fullmatch(re.sub(r"[\s.,!?]+", " ", _txt(amount_text).lower()).strip())
    # 'the minimum' / 'all of it' only in an offer: "I can't pay the full amount" is a no
    amount_is = ("floor" if m.group(1) else "full") if m and offer and amount is None else None
    if amount is None and not amount_is and (amount_text.strip() or kind in ("offer", "vague")):
        # nothing our parser reads: the amount the model wrote in digits (contract §5). Not
        # for digits we read but cannot settle ('200 or maybe 300'): that is no model's guess
        if not re.search(r"\d|\b(or|maybe|veya|ya da|yoksa|belki)\b", _fold(amount_text)):
            amount = _said_amount(v)
    # '200 or maybe 300', 'half of it', words nobody reads: asked again, never the full amount
    bad = amount is None and not amount_is and _amount_words(amount_text)
    said_yes = kind == "yes"
    if (kind in ("yes", "no", "too_much", "evasive") and (iso or amount is not None)) or (
        offer and (bad or amount_is)
    ):
        kind = "offer"  # a date or an amount the customer names is never a refusal
    if kind == "offer" and not iso and amount is None and not amount_is and dk == "vague":
        kind = "vague"
    prod = str(v.get("about_product") or "").strip().lower()
    # "p1, p2" (seen once) is not a single product: take the first one named
    codes = re.findall(r"p([1-9])", prod)
    pi = int(codes[0]) - 1 if codes else None
    raw = last_customer_text(v)
    t = _txt(raw)
    mixed = bool(raw and _re("refusal").search(_re("today_refusal").sub(" ", t)))
    if mixed and not _re("part").search(t):
        st0 = load_state(v) or {}
        mixed = bool(isinstance(st0.get("p"), list) and _named_in(st0, raw))
    allp = str(v.get("all_products") or "").strip().lower() in ("yes", "true", "all")
    if raw and _re("all").search(t) and not mixed:
        # "both" in "I cannot pay both" is no day for all of them
        allp = True
    cut = is_cut(raw)
    cut_kind = ""
    if cut and kind == "stop" and _re("stop").search(t):
        pass  # "I do not want to talk" is whole even without a full stop
    elif cut and kind in ("stop", "evasive", "no", "too_much") and not iso and amount is None:
        # half a sentence is no wish to end the call, no non-answer and no refusal yet ("I
        # cannot pay because my" may go on with a day): nothing is counted for now
        cut_kind = kind
        kind = "unclear"
    return {
        "kind": kind,
        "amount": amount,
        "amount_is": amount_is,
        "amount_bad": bad,
        "date": iso,
        "date_kind": dk,
        "all": allp,
        "product": pi,
        "mixed": mixed,
        "cut": cut,
        "cut_kind": cut_kind,
        "question": str(v.get("has_question") or "").strip().lower() in ("yes", "true"),
        "full_too_much": bool(raw and _re("full_too_much").search(t)),
        "consent_words": bool(raw and _re("consent").search(t)),
        "hear_only": bool(raw and _re("hear_only").search(t)),
        "hardship_words": bool(raw and _re("hardship").search(t)),
        "also_hardship": str(v.get("also_hardship") or "").strip().lower() in ("yes", "true"),
        "said_yes": said_yes,
        "raw": raw,
        "date_text": date_text,
        "amount_text": str(v.get("said_amount_text") or ""),
    }


# An enum variable is cleared to one of its values: the platform rejects '' ("variable
# 'has_question' rejected by schema validation", every turn), so the last turn's value stayed and
# a cut turn got "I do not have that information." again and again (talkative A PUB-05).
REPLY_CLEAR = dict(
    reply_kind="unclear",
    all_products="no",
    about_product="none",
    has_question="no",
    also_hardship="no",
)


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


def end_notes(v, st, neg, held):
    """A call our side ends (an error, a transfer that did not happen): with notes on, every
    product without a promise (ids not in `held`) gets its note, as _finish writes them; one
    still being talked about is a service_error. Sets the note_* variables."""
    rs = neg.get("reasons") or {}
    ks = ("refused", "hardship", "dispute", "paid")
    ps = sorted([q for q in st.get("p") or [] if isinstance(q, dict)], key=lambda q: q.get("n", 0))
    notes = [
        [q.get("id"), rs.get(s, s)]
        for q in ps
        if q.get("id") not in held
        for s in [q.get("status") if q.get("status") in ks else "service_error"]
    ]
    notes = notes if neg.get("notes") else []
    st["notes"] = notes
    v["neg_state"] = json.dumps(st)
    v["note_count"] = str(len(notes))
    if notes:
        v["note_product_id"], v["note_reason"] = notes[0]


def end_close(st, neg):
    """(outcome, reason) of a call our side ends with no promise held (a transfer that did not
    happen, SM-12): _finish's closing rule over the products' own ends, a product still being
    talked about counting as service_error."""
    oc = neg.get("outcomes") or {}
    ks = ("refused", "hardship", "dispute")
    ps = sorted([q for q in st.get("p") or [] if isinstance(q, dict)], key=lambda q: q.get("n", 0))
    left = [
        q.get("status") if q.get("status") in ks else "service_error"
        for q in ps
        if q.get("status") != "paid"
    ]
    if ps and not left:
        return oc.get("paid", "paid"), str((neg.get("close_reasons") or {}).get("paid") or "")
    s = left[0] if left else "service_error"
    if _pol(neg, "no_promise_reason") == "priority":
        s = next((x for x in _pol(neg, "reason_priority") if x in left), s)
    return oc.get("no_promise", "no_promise"), close_reason(neg, s)


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
