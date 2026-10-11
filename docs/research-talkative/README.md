# Talkative customers: research (11 Oct 2026)

Why this matters: the kit README says the final customers "talk like people. They give a number in
two parts, correct themselves, **talk about other things**, ask a question back...". The talkative
style is the one we fail most (round 2: A 7/10, B 0/3), and every case must pass 3 of 3 runs.

## Data and tools

| What | Where |
|---|---|
| Every talkative call, raw platform JSON (not committed: ~190 MB; `call_dump.py` writes it again) | `raw/<run>-<case>.json` |
| Same calls, one line per event (messages, node entries, extractions, routes, tool calls) | `timeline/<run>-<case>.txt` |
| One summary line per call (verdict, end reason, cut turns, re-asks, last node) | `calls.jsonl` |
| Non-talkative calls for comparison (pieces, asks_back, in_words, corrects, plain) | `compare/` |
| Independent audit of every talkative timeline (Fable) | `AUDIT_FABLE.md` |
| Dump tool | `builder/tools/call_dump.py <results.json>...` or `--sim <simulation_id>` |
| Platform probe for the raw last message | `builder/tools/probe_lastmsg.py` |

Runs analysed:

| Run | Agent | Calls |
|---|---|---|
| `style2-a-talkative` | brief-a, commit 6b28e2f | 10, 7 passed |
| `style2-b-talkative` | brief-ab, commit 6b28e2f | 3, 0 passed |
| `exp-wait2-b-talkative` | brief-ab, waitSeconds 2.0 experiment | 3, 1 passed |
| `sim-469a79a3` | brief-a, commit 60d8c96 (the run stopped locally, finished on the platform) | 10, **10 passed** (ledger scored afterwards, judge 10/10) |
| `sim-0209244a` | brief-ab, commit 60d8c96, run 1 of 3 | 3, 2 passed (PUB-13 failed: T15) |
| `style3-b-r2`, `style3-b-r3` | brief-ab, commit 60d8c96, runs 2 and 3 | 2 passed (PUB-11 failed: T2), 3 passed. Over 3 runs: PUB-12 3/3, PUB-11 2/3, PUB-13 2/3 |

## 1. What the simulated customer does (measured, outside our control)

The simulation runs in `mode: "chat"`: no audio, so nothing is cut by speech timing. The customer
is `gemini-3-flash-preview` with the persona prompt of `kit/tools/run_cases.py`.

**Customer messages arrive cut off.** A message that ends without `.`, `!`, `?` or a closing quote:

| Style | Cut customer messages |
|---|---|
| plain (llm-a-1, llm-b-1) | 0 / 75 = 0 % |
| in_words | 2 / 75 = 3 % |
| pieces | 6 / 112 = 5 % |
| corrects | 7 / 78 = 9 % |
| asks_back | 10 / 91 = 11 % |
| **talkative** | **33 / 74 = 45 % (A), 24 / 42 = 57 % (B)**; 113 of 214 in all talkative runs |

- Cut messages are short (median 8 words; full ones median 25). There is no fixed length limit:
  full messages of 30-39 words arrive whole.
- Cut messages come later (median 2.14 s after the agent; full ones 1.67 s, whatever their
  length). This fits a time or token budget on the persona model that its thinking uses up; the
  text written so far is sent as the turn.
- Leaked model text arrives as customer turns: `thought`, `Ready for the next turn.`,
  `*   "I've`, `model)\n    *   Previous turn 2:`, `Reviewing the prompt instructions:`.
- **Cuts are sticky**: after a cut turn the next one is cut 73 % of the time (39 % after a whole
  turn). The rate grows with the call: 6 % on the first turn, 23 % in turns 1-3, ~60 % later.
  The persona keeps restarting the same sentence, most often the one about the cousin's wedding
  (the style asks for it once; it never gets through whole, so the persona tries again):
  "My cousin's wedding is actually on the thirti" five times in a row (B PUB-13).
- Longest run of cut turns in one call: 13 (sim-469a79a3 PUB-04), 11 (B PUB-13, exp PUB-12),
  10 (sim-469a79a3 PUB-03).
- No agent wording was seen to break the run: after a cut turn the next one came through whole
  25-35 % of the time whatever the agent said ("Sorry, I did not get that" + repeat, short
  re-ask, explanation, read-back). Patience works: PUB-04 got its answer after 23 fragments and
  passed.
- **Hang-ups** (`customer-ended-call`) with no customer message, ~3 s after an agent turn: 3 of
  26 talkative calls (after 1, 11 and 11 cut turns). Most likely the persona produced nothing
  in time. None in non-talkative runs.
- The persona shows frustration as the agent repeats itself word for word ("This is getting
  really frustrating", "This is really annoying", sim-469a79a3 PUB-03).

Not the agent: a test with `startSpeakingPlan.waitSeconds` 2.0 (`exp-wait2`) had as many cuts.

## 2. Failures and near-misses on our side

Old agent = 6b28e2f, new agent = 60d8c96. "Fixed?" says whether the new agent already covers it.

| # | What happened | Evidence | Rule at risk | Fixed? |
|---|---|---|---|---|
| T1 | A fragment of small talk classified `declines`; two of them close `not_verified/declined` | A PUB-04 (7th fragment), B PUB-11 (`I'm so busy with all these emails coming in,`), A PUB-06 (passed by luck) | A15 | 60d8c96 made the description stricter; new run: all 29 cut turns at identity read as `unclear`, none as `declines`. Not yet proven over 3 runs |
| T2 | Identity has hard turn caps: `identity` 16, `identity2` 14 turns, then `fin_declined` | New PUB-03 used 13 turns in identity. **Failed** in style3-b-r2 PUB-11: 15 cut/small-talk turns after the phone question, all correctly `unclear`, the 16th hit the cap: closed `not_verified/declined` at 190 s | A15 | No |
| T3 | Re-ask after a fragment repeats the question word for word; the judge counts it as an extra "asked again" | B PUB-12: "phone question asked again twice" (wrong answer, then a fragment, then the same question) | A13/A14 (ask again once) | No |
| T4 | Same sentence repeated many times: frustration, hang-ups, judge "repetitive loop" | B PUB-13 read-back x12 then hang-up; exp PUB-12 read-back x67; new PUB-04 "Sorry, I did not catch that. Can you do that?" x20 | judge, hang-up | Partly (offer re-ask is short since 60d8c96; the read-back is still repeated whole: `phase == "confirm"` falls to `_readback`) |
| T5 | "I will pay the credit card tomorrow, but I am not paying the personal loan..." (cut) at the today question: the date was applied to **all** products, then a three-product read-back looped 67 times while the customer kept refusing two of them | exp PUB-12 | B4, B7 | No: `step()` applies an offer to every product when `phase == "ask_today"` and no product was extracted |
| T6 | A mixed answer at the read-back (one product yes, others no) is not understood: no per-product refusal | exp PUB-12 | B7 | No |
| T7 | Judge counts the combined "Can you pay all of them today?" as attempt 1 for every product, then a per-product attempt as attempt 2 | B PUB-12: "car loan (early band) received two attempts" | B4 (the brief says the opposite: start each product at attempt 1) | Judge varies (same flow passed in llm-b-1 and in_words). Wording can make the step explicit |
| T8 | Customer hang-up: `close_call` goes with `outcome: ""`, the bank answers `unknown outcome` | A PUB-01, B PUB-13, exp PUB-12 | A46 | No. Worse case not seen yet: a hang-up after a recorded promise would lose `promise` |
| T9 | One fragment after "Is there anything else I can help you with?" ends the call at once | new PUB-06, B PUB-12 (old) | (a question the customer still had would be lost) | No: `phase == "closing"` + unclear -> `_finish` |
| T10 | A cut "no" ("I can't do it that soon because I") used an attempt and moved to the next offer; the judge then saw "minimum offered twice" | A PUB-06 (old) | A26 | 60d8c96: short re-ask, no amount repeated. New PUB-06 passed |

| T11 | **code_exec size limit**: "Code and data for code_exec total 131136 bytes, over the 131072-byte limit a sandboxed process can receive". The data includes `vars`, and `vars['system.conversation']` grows every turn; our code nodes embed the whole library. From 134.8 s on, `decide` failed 30 times | exp PUB-12 (55+ turns) | all | No. Long talkative calls are exactly where it bites |
| T12 | `about_product` returned "p1, p2" -> `enum_failed`, value dropped; this is what sent T5's date to all products | exp PUB-12 34.6 s | B4 | No |
| T13 | "These questions protect your account" said 5 times on unclear turns (the line is for a refusal, once) | new PUB-05 | A15 ("explain once") | No |
| T14 | `id_heard` collects stale answers and leaked persona text across turns | A PUB-03/04 (old) | A13 | No wrong answer was burnt yet |

| T15 | "I would like to pay **both** the credit card and the loan on the thirtieth of October" (whole message): `all_products` not extracted, so the date went to the card only; the loan then got attempt 1 (16 Oct) instead of "the latest date for the loan is 23 October"; the customer accepted 16 Oct; wrong date recorded | sim-0209244a PUB-13 (new agent, B). The old agent read the same sentence right (`all_products` was set) | B3, B7; case check "loan recorded on its own deadline" | No. Extraction varies; a word check on the raw message ("both", "all of them", "all three", "everything") makes it deterministic |

Found by the independent audit (`AUDIT_FABLE.md`, all 26 timelines): T11-T14, the confirm-phase
re-ask being dead code in `_short` (T4), and that Brief B has never run on 60d8c96 with this
persona.

Paths checked and fine: third party (each turn re-enters `third_party` via `cb_check`, so the
8-turn cap is never reached; a cut message that holds the answer, "tomorrow morning around
eleven. I", was read correctly); opening (cut rate 6 %; "Yes, this is Lena Hoffmann. I've" is
read as `confirms`); negotiation turn caps (every turn goes to `decide`).

## 3. New platform facts (probe, 11 Oct)

`builder/tools/probe_lastmsg.py`, workflow slot `probe-lastmsg`, chat route:

| | Question | Result |
|---|---|---|
| L1 | Can a code node read the conversation? | **Yes.** `vars['system.conversation']` is a list of `{"role", "content"}` up to and including the customer's last message, word for word with its punctuation ("I have been so busy today because my") |
| L2 | Is `{{system.conversation.last-1}}` filled inside code_exec source? | No (left as is) |
| L3 | Is it filled in a tts line? | Yes ("1. USER: I have been so busy today because my") |

So a code node can tell a cut message from a whole one without asking the model, and can check
a refusal against the customer's own words.

Also seen: the agent config carries `sttConfig.language` / `ttsConfig.language` `tr`, voice
`leyla`, and `waitSeconds` 2 left from the experiment. The chat simulation does not use them.

## 4. What to change (ranked)

Principle: a cut message is a line problem, not an answer. It must never use up a counter that
can close the call (decline, attempt, unclear-at-opening), and the agent must stay in the call,
say less each time, and not say the same sentence twice in a row.

1. **Cut detection in code (L1).** `cut_last()` in the library: the last `user` entry of
   `vars['system.conversation']` has no end mark. In `decide`: a cut turn whose class is `no`,
   `evasive`, `stop`, `too_much` or `unclear` becomes `cut` (no attempt, no counter). A cut turn
   whose class is `yes` / `offer` / `paid` / `hardship` / `human` keeps it (the content arrived:
   "...spreadsheets. Yes", "tomorrow morning around eleven. I").
2. **Re-ask wording for a cut turn** (T3, T4): short, never word for word the last sentence,
   rotating: "Sorry, I think you were cut off. <short question>", "Sorry, I missed the end of
   that. <short question>", "Take your time. <short question>". At the read-back:
   "Sorry, I did not catch that. Do you confirm the payment I read out?" (T4).
3. **Multi-product** (T5, T6, T15): an offer at the today question applies to all products only
   when the customer says so, read both from `all_products` and from the raw message words
   ("both", "all of them", "all three", "everything"; L1), so a missed extraction does not drop it; otherwise to the product named, else the first one, and
   the next products are asked one by one. At the read-back, a refusal of named products takes
   them out of the promise (new extraction `refused_products`, p-codes).
4. **Hang-up outcome** (T8): `close_call` pre-hook fills an empty outcome from the state: a
   recorded promise -> `promise`; a transfer -> `transferred` + its reason; verified ->
   `no_promise/refused`; otherwise `not_reached/unclear`.
5. **Identity caps** (T2): `identity` / `identity2` caps well above any patience we need (the
   call limit, 420 s, ends it anyway); the cap target stays `fin_declined`. And (T1) a guard:
   `declines` routes through a code check that needs a whole (not cut) message.
6. **Closing** (T9): one fragment after "anything else" gets one short re-ask, then goodbye.
7. **Code size** (T11). Measured: `decide`, `facts`, `after_record`, `after_note` carry ~63 KB of
   code each (the whole library); in the 80-turn call the conversation was ~37 KB and the
   variable table ~41 KB, ~141 KB in all against 131 KB, first failure near customer turn 55.
   Real talkative calls of the new agent stay under ~30 customer turns, so only a stuck loop
   reaches it today. Embed only the functions each node needs (or a smaller library) so a
   100-turn call stays under 131072 bytes. It breaks the engine itself, not one rule, so it
   stays on the list even though fixes 3 and 2 remove the loops that reached it.
8. **Small items**: `about_product` accepts "p1, p2" (text var parsed in code) (T12); the
   protect line only on `declines`, once (T13); `id_heard` only from turns with an answer (T14).
9. **Today question wording** (T7): after a no to all of them, say "Let us go through them one
   at a time." before the first per-product offer.

Each change gets an engine test first (`tests/hack`, with cut turns replayed from the
timelines), then a simulation check.

## 5. How to measure

- Talkative A and B with `--reps 3` before and after, same commit for A and B, dump every run with
  `call_dump.py` (the `calls.jsonl` counters: cut turns, re-asks, hang-ups, last node).
- Then the other four styles once each to show nothing broke.
- A pass needs 3/3 per case; a run where the persona hangs up after >10 cut turns is recorded
  as such (cause outside the agent), but still counts as a fail.

## Blocker (11 Oct)

From this machine the bank mock (`freya-bank-mock.freyavoice.workers.dev`) is not reachable:
HTTP gets `307 Location: http://10.106.7.14/` with `Via: 1.0 middlebox`, HTTPS fails the
handshake. A filter on this network. Freya itself still reaches the bank (calls work), but
`run_cases.py` needs the bank for practice sessions and ledgers. A VPN or another network is
needed for any new measured run.
