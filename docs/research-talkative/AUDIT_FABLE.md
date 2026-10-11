# Talkative-persona audit (all 26 timelines)

Scope: every file in `docs/research-talkative/timeline/` read line by line (3 549 lines).
`style2-*` = agent at commit `6b28e2f` (13 calls), `exp-wait2-*` = same era with `waitSeconds` 2.0 (3 calls, Brief B),
`sim-469a79a3-*` = agent at commit `60d8c96` (10 calls, Brief A only, judge verdict only).
Baseline: `compare/timeline/` (65 non-talkative calls) used only for the cut-rate comparison.
Code references are to `builder/fb/nodecode/negotiation_lib.py` and `builder/fb/generate.py` at HEAD (`fe9442e`).

Timestamps below are the seconds column of the timeline files.

## 0. Headline numbers

| | style2 (old, A) | style2 (old, B) | exp-wait2 (old, B) | sim-469a79a3 (new, A) |
|---|---|---|---|---|
| pass | 7/10 | 0/3 | 1/3 | 10/10 |
| hang-ups (customer-ended-call) | 1 (PUB-01) | 1 (PUB-13) | 1 (PUB-12) | 0 |
| longest call | 45 s | 52 s | 201 s | 78 s |

- Customer turns delivered cut: **180 of 335 (54 %)** with this persona, versus 22 of 431 (5 %) over the five other styles in `compare/`. The talkative style is an order of magnitude noisier than anything else the agent was tuned on.
- Max consecutive cut turns in one call: **11** (exp-wait2 PUB-12 at 36.9-85.7; style2-b PUB-13 at 20.2-45.6), then 9 (sim PUB-03, sim PUB-06), 7 (style2-a PUB-02), 6 (sim PUB-04), 5 (sim PUB-01, PUB-05, PUB-09, style2-a PUB-04, style2-b PUB-12). Full per-call table in section 6.
- Brief B has **never been run with the newest agent** under this persona. Every Brief B talkative run on record failed except exp-wait2 PUB-13, and the bug behind the worst one (section 1.1) is in code that `60d8c96` did not touch.

## 1. Behaviours that caused a failure

### 1.1 A product-specific offer with a refusal of the other products is applied to all products (Brief B)

- File: `exp-wait2-b-talkative-PUB-12.txt`, 33.9-34.8.
- Customer (cut): `I've been buried under paperwork all morning and my cousin's wedding is on the thirtieth. I will pay the credit card tomorrow, but I am not paying the personal loan [CUT]`
- Extracted: `said_date_text=tomorrow`, `reply_kind=offer`, `about_product` **extraction_failed (enum_failed, value "p1, p2")** so `product=None`.
- Agent: `To confirm: the credit card ending 9015, 510.00 on Thursday 15 October 2026; the personal loan, 1320.00 on Thursday 15 October 2026; the car loan, 140.00 on Thursday 15 October 2026. Do you confirm?`
- Cause (code): `step()` calls `_apply_offer(..., all_products = multi and amount is None and (r["all"] or (phase == "ask_today" and r["product"] is None)))`. In `ask_today` with no product extracted, a date is applied to **every** open product. Nothing in the message said "all"; `all_products` was empty.
- Consequence: the call entered `confirm` with three agreed products. Every later message was `I'll pay the credit card tomorrow, but I'm not paying the personal loan or the car loan` (45.0, 54.8, 97.6, 102.5, 104.5, 106.4, 108.4, 113.6, 115.5, 121.3, 126.8 ... 200.0, at least 25 times). Each was extracted `reply_kind=offer, about_product=p1, said_date_text=tomorrow`. In `confirm` phase an `offer` on an already-agreed product with the same date changes nothing, so `_apply_offer` returns `None` and the engine re-reads the same three-product read-back. `read_reply` converts any `no` that carries a date into `offer` ("a date the customer names is never a refusal"), so the refusal of p2/p3 can never be heard in this phase. At 134.8 the `decide` code node began failing on every turn (`Code and data for code_exec total 131136 bytes, over the 131072-byte limit`) and the engine kept re-saying the read-back from the stale state. The customer hung up at 201.4. `close_call` was sent with `outcome: ""` (`unknown outcome`), no notes, no promise.
- At risk: Brief B rules 4, 7, 8; PUB-12 checks "exactly one promise, on the credit card", "one refused note for each of the other two products", "two attempts on the personal loan, one on the car loan"; Brief A rule 46 (exactly one close_call with an outcome).
- Frequency: 1 of 2 Brief B PUB-12 runs. The older run (`style2-b-talkative-PUB-12`, 20.9) got `about_product=p1` from a complete message and went the right way; the failure needs a cut right after "personal loan" (so the extractor sees two products) or any message naming two products. This is live in HEAD.

### 1.2 Small-talk fragments classified as `declines` (old agent only; fixed in `60d8c96`)

Every instance, all with the old prompt text ("declines: the customer refuses to answer ... asks what the call is about"):

| file | t | customer (cut) | var |
|---|---|---|---|
| style2-a PUB-03 | 13.4 | `I'm so sorry, I'm just distracted [CUT]` | id1=declines 1.0 |
| style2-a PUB-04 | 31.9 | `I am so sorry, I was just thinking about my cousin [CUT]` | id1=declines 0.99 |
| style2-a PUB-04 | 37.6 | `I have been running around all day trying to get everything done [CUT]` | id2=declines 1.0 -> fin_declined |
| style2-a PUB-06 | 10.1 | `I've been running around all day trying to get things sorted for my cousin'` | id1=declines 1.0 |
| style2-a PUB-07 | 11.5 | `The traffic was just terrible getting home [CUT]` | id1=declines 1.0 |
| style2-a PUB-08 | 6.0 | `The traffic was absolutely terrible this morning [CUT]` | id1=declines 1.0 |
| style2-b PUB-11 | 12.2 | `I'm so busy with all these emails coming in, [CUT]` | id1=declines 1.0 |
| style2-b PUB-11 | 14.7 | `I've been trying to clear my desk all morning [CUT]` | id2=declines 1.0 -> fin_declined |
| exp-wait2 PUB-11 | 11.3 | `I have just been running from one thing [CUT]` | id1=declines 1.0 |
| exp-wait2 PUB-11 | 14.1 | `I am so sorry, I am just so distracted [CUT]` | id2=declines 1.0 -> fin_declined |

10 instances in 7 calls; 3 calls closed `not_verified/declined` (style2-a PUB-04, style2-b PUB-11, exp-wait2 PUB-11) which are 3 of the 8 old-agent failures. The other 7 instances only cost the "These questions protect your account" line and a node change. Brief A rule 15 at risk (declining needs a refusal; two of them close the call).
Newest agent: 0 `declines` extractions over roughly 60 cut identity turns (sim PUB-01, 03, 04, 05, 06, 09, 10). The prompt fix holds on this sample.

### 1.3 Full offer repeated after a cut, counted as a second attempt / second floor offer (old agent; partly fixed)

- `style2-a-talkative-PUB-06.txt` 20.6-23.5 (FAIL, judge: "minimum payment offered twice (Message 12 and Message 14)"):
  - 20.6 Agent: `The minimum for the credit card ending 1903 is 637.50. Could you pay 637.50 on Wednesday 14 October 2026? The rest stays overdue.`
  - 22.9 Customer: `I can't do it that soon because I [CUT]` -> `reply_kind=no conf=0.59`
  - 23.5 Agent: `Could you pay 637.50 for the credit card ending 1903 on Friday 16 October 2026? The rest stays overdue.`
  - Two agent-side causes: (a) a fragment that is the beginning of a date counter-offer was read as a flat `no` (confidence 0.59, above the 0.5 floor), which burnt attempt 1; (b) attempt 1 restated the floor amount, so the judge saw the minimum offered twice even though Brief A rule 26/30 allows the attempt. The persona script ("accepts the minimum payment for this Friday") guarantees that a floor offer dated today is turned down, so the agent's own wording makes the second mention of 637.50 almost inevitable. PUB-06 check "minimum payment offered once" at risk.
- `style2-b-talkative-PUB-12.txt` 21.2-24.2 (FAIL, judge: "personal loan received three attempts (Messages 10, 14, 18)"):
  - 21.2 Agent: `I have noted the credit card ending 9015 for 510.00 on Thursday 15 October 2026. Could you pay 1320.00 for the personal loan on Friday 16 October 2026?`
  - 23.2 Customer: `I'm just overwhelmed with all these deadlines [CUT]` -> `unclear`
  - 24.2 Agent: `Sorry, I did not get that. I have noted the credit card ... Could you pay 1320.00 for the personal loan on Friday 16 October 2026?`
  - The judge counted the repeat as an extra attempt. Same pattern with the opening statement: `Sorry, I did not get that. I am calling from the collections team ... Can you pay all of them today?` repeated 8 times in exp-wait2 PUB-12 (14.6-32.0), 3 times in exp-wait2 PUB-13, 2 times in style2-b PUB-13, once each in style2-a PUB-05 and style2-b PUB-12.
  - `60d8c96` replaced the repeat with `_short()` ("Sorry, I did not catch that. Can you do that?" / "Can you make this payment today?"). Confirmed in sim PUB-04, 06, 07, 10. Not fixed in the confirm phase, see 2.1.

### 1.4 Post-cut identity re-ask counted as "asked again" (old agent; still possible)

- `style2-b-talkative-PUB-12.txt` 9.7-12.2 (judge: "The phone question was asked again twice (Messages 6 and 8)"):
  - 9.7 Agent: `That answer is not correct. What are the last four digits of the phone number we have on file?`
  - 11.8 Customer: `Oh, I must have misspoke, I'm [CUT]` -> `id1=unclear`
  - 12.2 Agent: `What are the last four digits of the phone number we have on file?`
  - PUB-12 check "the phone question is asked again once, the date of birth is not" at risk. The identity node is an LLM conversation node; the newest agent still re-asks the full question after every cut (sim PUB-01 9.0-19.4 six times, sim PUB-03 12.0-38.5 ten times, sim PUB-09 10.2-31.1 seven times). A judge that counts re-asks literally has 6-10 of them to count. The newest agent has not met a wrong-then-cut sequence yet, so the exact failure is unreproduced on it; the exposure is unchanged.

### 1.5 Unbounded identical read-back loop until the customer hangs up (old agent; not fixed in the confirm phase)

- `style2-b-talkative-PUB-13.txt` 18.2-46.2: the same read-back `To confirm: the credit card ending 6604, 300.00 on Friday 30 October 2026; the personal loan, 900.00 on Friday 23 October 2026, which is the latest date I can record for it. Do you confirm?` spoken **12 times** against 11 consecutive cuts; customer hung up at 49.2; `close_call outcome ""`. The read-back itself was correct for PUB-13 (both dates named). The judge's complaint was the loop.
- Also exp-wait2 PUB-12 (section 1.1): the three-product read-back spoken 67 times.
- Code: in `step()`, the `confirm` block ends with `st["say"] = _readback(st, neg)` for every kind that is not yes/offer/no. The generic `unclear` branch (`"Sorry, I did not catch that. " + _short(...)`) is further down and never reached in `confirm`. `_short()` has a `confirm` case ("Do you confirm?") that is therefore dead code for this path. The newest agent shows exactly this: sim PUB-07 23.8, 31.1, 34.9, 39.0 repeats the full read-back (amount, date) 4 times; sim PUB-06 45.9 (LLM `negotiate` node) `Sorry, I did not catch that. To confirm: the credit card ending 1903, 637.50 on Friday 16 October 2026. Do you confirm?`.

### 1.6 Customer hang-up leaves `close_call` with an empty outcome

- All three hang-ups: style2-a PUB-01 14.3, style2-b PUB-13 49.2, exp-wait2 PUB-12 201.4 -> `close_call {"outcome": ""}` -> bank: `{"ok": false, "error": "unknown outcome"}`. Ledger: `outcome (None, None)`. Brief A rule 46 is broken on every hang-up regardless of what happened before. The notes required by Brief B rule 8 were also never written in exp-wait2 PUB-12.

## 2. Near-misses (passed, but a stricter judge could fail them)

### 2.1 Amount and date repeated in the confirm phase (newest agent)

- sim PUB-07 23.8/31.1/34.9/39.0: `To confirm: the personal loan, 540.00 on Tuesday 27 October 2026. Do you confirm?` x4 against three cuts (`I'm just trying to get through this pile of paperwork [CUT]`, `I've been staring at this computer screen for [CUT]`, `I've been so busy with these clients all afternoon, it's just one meeting [CUT]`).
- sim PUB-06: 637.50 spoken in 15.7 (floor offer), 42.6 (LLM confirm), 45.9 (LLM re-confirm after cut), 49.1 (recorded). Check "minimum payment offered once" survived because the judge read the last three as a read-back, not an offer. Thin margin.
- style2-b PUB-12 31.3-44.1: read-back x6 (passed that check, failed others).
- Frequency: every call that has a cut during confirmation: 6 of 26 calls; 3 in the newest agent's 10.

### 2.2 Contentless "Can you do that?" repeated up to 20 times (newest agent)

- sim PUB-04 15.5-68.5: `Sorry, I did not catch that. Can you do that?` **20 times** in a row (17 cut turns, including simulator leaks `*   "I've [CUT]` at 14.9 and `Ready for the next turn.` at 17.2). The proposal it refers to (`The latest date I can record for the car loan is Friday 23 October 2026. Could you pay 1250.00 on that day?`) was last heard 55 s earlier. The customer recovered at 70.1 with `Am I talking to a robot, and yes, I can pay on the twenty-third of October.`
- sim PUB-06 18.5-40.1: 9 times. sim PUB-10 22.4/24.7: 2 times.
- Risk: the two long hang-ups in this set both followed runs of identical agent lines (12 and 60+). The newest agent survived 20, so the hang-up threshold is not known; evidence that 20 is safe is one call.

### 2.3 Identity node turn cap closes as `declined` without the app/branch line

- `generate.py`: `identity` conv node `max_turns=16, cap_to="fin_declined"`; `identity2` `max_turns=14`.
- sim PUB-03 used **13 customer turns** in `identity` (3.9 to 40.4; 9 consecutive cuts 11.8-37.5, two of them with frustration: `This is getting really frustrating because I have a mountain of paperwork [CUT]` 21.5, `This is really annoying because I've been on my feet [CUT]` 28.9). Three more cuts and the call would have closed `not_verified/declined` saying only `Thank you for your time. Goodbye.` -- wrong reason for PUB-03 (expected `failed`) and the check "customer told to use the app or a branch" would fail. sim PUB-09 used 9 turns, sim PUB-01 and PUB-05 7 each.
- Old agent: style2-a PUB-04 used 9+2 turns before the misclassified decline.

### 2.4 "These questions protect your account" said on every cut (newest agent)

- sim PUB-05 9.8, 12.5, 14.8, 17.5, 24.7: `These questions protect your account. Could you tell me the last four digits ...` x5 in reply to `unclear` fragments (`I've been so busy looking for work lately that I [CUT]`). Brief A rule 15 says explain **once**, and only when the customer does not want to answer. The LLM identity node picked the line itself; `id1` was `unclear` each time, so the engine's `identity2` explanation (which is the "once") is still unused and would be said a sixth time on a real decline. Also "looking for work" in the identity phase was not acted on (correct: hardship is a negotiation-phase rule).

### 2.5 A fragment read as `no` burns a persuasion attempt

- style2-a PUB-06 22.9 (section 1.3, failed).
- style2-b PUB-13 15.2: `I'm sorry, but I can't [CUT]` -> `reply_kind=no conf=1.0` -> attempt 1 on the credit card (early band, **one attempt**): `Could you pay 300.00 for the credit card ending 6604 on Friday 16 October 2026?`. The customer then offered 30 October, so nothing was lost, but had she refused again the card would have been closed `refused` on the strength of a four-word fragment. In the newest agent `read_reply` still accepts `no` at confidence >= 0.5 and the enum description says nothing about truncated "I can't ...".
- Not observed in the newest agent's 10 calls (its `no`s were all complete sentences: sim PUB-10 19.3, sim PUB-05 34.6 `I don't want to be transferred [CUT]` which is a correct `no`).

### 2.6 `hardship` inferred from a fragment

- sim PUB-05 32.1: `I've been so stressed out looking for work every day and the traffic is just terrible. I [CUT]` -> `reply_kind=hardship conf=1.0`. Correct for this persona (he lost his job) but "looking for work" is not "lost my job"; a different case with the same small talk would get a sympathy line and skip the persuasion attempt (Brief A rule 32 before rule 30). One instance.

### 2.7 Call closed on a fragment at "Is there anything else I can help you with?"

- 8 instances: style2-a PUB-06 31.1, style2-a PUB-07 43.4, style2-b PUB-12 48.4, exp-wait2 PUB-13 36.1 (`Reviewing the prompt instructions: - "Stay in [CUT]`), sim PUB-01 29.7 (`viewthoughtI'm just about [CUT]`), sim PUB-06 51.2, sim PUB-07 45.4, sim PUB-10 39.4. In each the engine's `closing` phase maps `unclear` to `finish` and says goodbye. No judge objected; the brief has no rule against it, and it is the one place where closing on `unclear` prevents loops. Listed for completeness; I would not change it.

### 2.8 Offer re-asked for a date the customer already gave

- exp-wait2 PUB-12 91.0-93.5: `I'll pay the credit card [CUT]` -> `offer` with no date -> Agent: `On which day can you pay 510.00?` although p1 was already agreed for 15 October from 34.6. Minor; one instance; a side effect of 1.1.

### 2.9 Third-party loop on cuts (old agent, passed)

- style2-a PUB-02 5.3-22.3: 7 consecutive cut turns, each `tp_reply=other`, each followed by a code node reporting `variable 'tp_reply' rejected by schema validation`. The agent varied its wording (`I'm sorry to hear that.`, `That sounds lovely.` to `My cousin's wedding is on the 30th [CUT]`), never leaked the reason for the call, and booked 2026-10-15 11:00 at 29.5. Within Brief A rule 5. `third_party` has `max_turns=8` with the cap routing to `cb_check`, which comes back to `third_party`, so no hard stop. Newest agent sim PUB-02 took 2 turns.

## 3. How the persona recovers from a cut

In every recovery the complete message is the **same sentence the fragments started**, finished: the simulator re-generates the turn rather than continuing from the fragment. What the agent said just before made no visible difference to the content of the recovery; it only had to keep the turn open and not change state.

| file | re-asks before the full message | agent line immediately before recovery | full message |
|---|---|---|---|
| style2-a PUB-02 | 7 | `I'm sorry to hear that. When can Ms Costa be reached?` | `... You can reach Maria tomorrow morning around eleven, but I can just pay it myself [CUT]` (cut again, but after the answer) |
| style2-a PUB-04 | 7 then 2 | `What are the last four digits ...` | never recovered (closed as declined) |
| style2-a PUB-07 | 4 | `What are the last four digits ...` | `... The last four digits are seven zero nine three.` |
| style2-b PUB-12 | 6 | `To confirm: ... Do you confirm?` | `... Yes, I confirm.` |
| style2-b PUB-13 | 12 | `To confirm: ... Do you confirm?` | never (hang-up) |
| exp-wait2 PUB-12 | 8 | `Sorry, I did not get that. I am calling from the collections team ...` | `... I will pay the credit card tomorrow, but I am not paying the personal loan [CUT]` |
| exp-wait2 PUB-13 | 3 then 3 | `Sorry, I did not get that. Could you pay 300.00 ... Friday 16 October 2026?` | `... I'd like to pay both of those on the thirtieth of October.` |
| sim PUB-01 | 5 | `I understand. Could you tell me the last four digits ...` | `Work is just non-stop today and I have my cousin's wedding on the 30th to prepare for. The last four digits are 4471.` |
| sim PUB-03 | 9 | `I'm sorry, I didn't catch that. Could you tell me your date of birth, please?` | `... My date of birth is 21 July 1990. How much do I owe anyway?` |
| sim PUB-04 | 20 | `Sorry, I did not catch that. Can you do that?` | `... Am I talking to a robot, and yes, I can pay on the twenty-third of October.` |
| sim PUB-05 | 5 | `These questions protect your account. Could you tell me ...` | `... The last four digits are eight eight four six.` |
| sim PUB-06 | 9 | `Sorry, I did not catch that. Can you do that?` | `... Can I pay that minimum amount this Friday instead?` |
| sim PUB-07 | 3 | `To confirm: the personal loan, 540.00 on Tuesday 27 October 2026. Do you confirm?` | `... Yes, I confirm.` |
| sim PUB-09 | 7 | `Could you tell me the last four digits ...` | `... The last four digits are five five six zero.` |
| sim PUB-10 | 2 | `Sorry, I did not catch that. Can you do that?` | `... Yes, I can pay the full amount this Friday.` |

Two observations: (1) the longest recoveries happen in the identity phase and after an offer, not after the opening question; (2) when the customer's recovered message contains a question (sim PUB-03 `How much do I owe anyway?`, sim PUB-04 `Am I talking to a robot`), the newest agent handled it correctly (closed without disclosing; answered "digital assistant" then confirmed).

## 4. Fragments that contained part of the real answer

- `style2-b-talkative-PUB-11.txt` 7.4: `I've been juggling so many meetings all morning, it's been quite a day. The last four digits are two [CUT]` -> `id1=answers, id_heard=two, id_last=two`. Agent 7.7: `And the remaining digits?` (correct per the `6b28e2f` partial-answer rule; `verify_identity` was not called, no wrong answer burnt). Then 9.8 `I'm sorry, I got distracted [CUT]` -> `unclear` -> `And the remaining digits?`; 12.2 `I'm so busy with all these emails coming in, [CUT]` -> `declines` (old prompt) -> identity2 -> 14.7 `declines` -> closed. The partial-answer handling was right; the decline misclassification (1.2) killed the call.
- `style2-a-talkative-PUB-03.txt` 8.6: `*   July 19 [CUT]` (leaked bullet) -> `id1=answers, id_last=July 19, id_heard=twelve July nineteen ninety one July 19` -> Agent 11.3 `And the year?` (correct). Next turn `I'm so sorry, I'm just distracted [CUT]` -> `declines` -> identity2 asked the whole DOB again; the persona then gave its second wrong answer. Note `id_heard` concatenated the previous wrong answer with the new fragment; had the year arrived, the pre-hook would have had to pick the right pieces out of `twelve July nineteen ninety one July 19 <year>`. Thin evidence of harm, but the accumulation rule is fragile.
- `style2-a-talkative-PUB-04.txt` 38.4: `id_heard=The traffic was absolutely terrible this morning, it took me model) I am so sor...` -- simulator leak text accumulated into the answer buffer. Not sent to the bank in this call.
- No fragment in the newest agent's 10 calls contained a partial digit string; the one partial date (`sim PUB-03` 8.6 old-agent case above) is the only "And the year?" seen.

## 5. Things checked and found clean

- The wedding date ("the 30th") was **never** extracted as a payment date in 26 calls: `said_date_text` stayed empty on `My cousin's wedding is actually on the thirti [CUT]` x6 (style2-b PUB-13), and was `Monday the 26th` / `the week after next` when the wedding was mentioned in the same sentence (style2-a PUB-07 28.0, sim PUB-07 15.9). In exp-wait2 PUB-13 29.1 the persona itself asked for the 30th, which is the case script.
- Simulator leaks (`thought`, `viewthought`, `Ready for the next turn.`, `*   Previous turn 2:`, `Reviewing the prompt instructions:`) were always `unclear` (6 instances) or produced no extraction at all (style2-a PUB-04 24.9).
- `create_promise` retry (PUB-10) worked under the persona both times; "recorded" was said only after `ok: true`.
- Rule 13 (day/month swap), rule 43 (scam) and the human-transfer path never came up with this persona; no evidence either way.

## 6. Per-call table

| file | pass | ended | dur s | cust turns | cut | max consec. cut | max identical agent line |
|---|---|---|---|---|---|---|---|
| style2-a PUB-01 | F | customer | 14 | 3 | 1 | 1 | 1 |
| style2-a PUB-02 | P | assistant | 30 | 8 | 7 | 7 | 2 |
| style2-a PUB-03 | P | assistant | 21 | 6 | 2 | 2 | 2 |
| style2-a PUB-04 | F | assistant | 39 | 11 | 9 | 5 | 7 |
| style2-a PUB-05 | P | assistant | 18 | 6 | 1 | 1 | 1 |
| style2-a PUB-06 | F | assistant | 32 | 10 | 4 | 2 | 2 |
| style2-a PUB-07 | P | assistant | 45 | 13 | 5 | 4 | 4 |
| style2-a PUB-08 | P | assistant | 20 | 5 | 1 | 1 | 1 |
| style2-a PUB-09 | P | assistant | 18 | 4 | 0 | 0 | 1 |
| style2-a PUB-10 | P | assistant | 33 | 8 | 2 | 1 | 1 |
| style2-b PUB-11 | F | assistant | 16 | 6 | 4 | 4 | 2 |
| style2-b PUB-12 | F | assistant | 52 | 18 | 8 | 5 | 6 |
| style2-b PUB-13 | F | customer | 49 | 18 | 12 | 11 | 12 |
| exp-wait2 PUB-11 | F | assistant | 15 | 5 | 3 | 3 | 1 |
| exp-wait2 PUB-12 | F | customer | 201 | 80 | 45 | 11 | 67 |
| exp-wait2 PUB-13 | P | assistant | 37 | 13 | 6 | 3 | 3 |
| sim PUB-01 | P | assistant | 33 | 11 | 6 | 5 | 3 |
| sim PUB-02 | P | assistant | 7 | 2 | 2 | 2 | 1 |
| sim PUB-03 | P | assistant | 43 | 14 | 9 | 9 | 9 |
| sim PUB-04 | P | assistant | 78 | 27 | 17 | 6 | 20 |
| sim PUB-05 | P | assistant | 36 | 10 | 7 | 5 | 5 |
| sim PUB-06 | P | assistant | 52 | 18 | 12 | 9 | 9 |
| sim PUB-07 | P | assistant | 48 | 13 | 6 | 3 | 4 |
| sim PUB-08 | P | assistant | 13 | 4 | 0 | 0 | 1 |
| sim PUB-09 | P | assistant | 44 | 11 | 6 | 5 | 7 |
| sim PUB-10 | P | assistant | 42 | 11 | 5 | 2 | 2 |

Hang-ups and what preceded them:
1. style2-a PUB-01 at 14.3 s: one cut turn, agent had just said `I'm sorry, I didn't catch that. What are the last four digits of the phone number we have on file?` (its second ask). No frustration signal, no loop. Most likely a simulator drop; thin evidence for any agent cause.
2. style2-b PUB-13 at 49.2 s: 12 identical read-backs against 11 consecutive cuts; no explicit frustration text, the fragments were small talk and the wedding.
3. exp-wait2 PUB-12 at 201.4 s: 67 identical read-backs; persona text degenerated into `Short of that, I can't help you.` repeated 8 times per message from 157.6; `decide` had been failing on payload size since 134.8.
Explicit frustration before a hang-up was seen only in sim PUB-03 (`This is getting really frustrating`, `This is really annoying`), which did **not** hang up.

## 7. Ranked agent-side changes

Ordered by (expected failures removed) x (confidence in the evidence). "Evidence" lists the calls; "thin" means one call or inference from code only.

1. **Never apply a product-specific date to all products unless `all_products == yes`** (`negotiation_lib.step`, the `all_products=` argument to `_apply_offer`). When `about_product` is empty, apply the offer to the current product `st["i"]` (Brief B rule 4 goes in `get_account` order, so this is p1 first, which is what PUB-12 needs). Additionally make `about_product` tolerant of two values: change the enum to a text var and parse the first `p[1-3]`, or add a second enum `refuses_product`. Evidence: exp-wait2 PUB-12 34.6 (`enum_failed "p1, p2"` -> three products agreed). Removes the only unrecoverable Brief B failure. Confidence high (code path read, one call reproduces it).

2. **In `confirm`, an `offer` that names only product X while others are agreed must release the others** (status back to `open`, then run `_flat_refusal` on them in order, counting attempts) instead of re-reading the same read-back. Also break the identical-say loop generally: if `st["say"]` equals the previous say three times in `confirm`, say `Which promise would you like to change?` once (already exists for `no`) and, if that too is unanswered, fall back to the per-product question. Evidence: exp-wait2 PUB-12 45.0 onward (25+ identical messages, each ignored); the `no`-with-date-to-`offer` rule in `read_reply` makes the refusal unhearable in this phase. Confidence high for the cause, medium for the exact repair.

3. **Route `unclear` in `confirm` through `_short()`** (`"Sorry, I did not catch that. Do you confirm?"`) instead of `_readback`. Allow one full read-back repeat (e.g. on the 3rd consecutive unclear) so the customer can still hear what they are confirming, then back to the short line. Evidence: sim PUB-07 x4, sim PUB-06 45.9, style2-b PUB-12 x6, style2-b PUB-13 x12, exp-wait2 PUB-12 x67. Fixes the near-miss 2.1 and removes the amount-repeat exposure on PUB-06. Confidence high.

4. **Fallback outcome on customer hang-up** so `close_call` never carries `outcome: ""`: before verification `not_verified/declined` (or `not_reached/unclear` if the name was never confirmed), during negotiation with nothing recorded `no_promise/refused`, after a recorded promise `promise`; write the Brief B notes for open products first. Evidence: all three hang-ups returned `unknown outcome`. This does not make a hung-up case pass, but it stops Brief A rule 46 from failing on top, and makes the ledger readable. Confidence high, cheap.

5. **Deterministic identity re-ask after a cut, without the question text** (`identity`/`identity2` are LLM nodes; move the re-ask to the engine): on `unclear` say `Sorry, I did not catch that, please go on.` and keep the question open; repeat the full question only every third unclear. Reserve `These questions protect your account` for `declines` (engine-owned, said once). Evidence: style2-b PUB-12 judge counted the post-cut re-ask as a second re-ask (check "asked again once"); sim PUB-05 said the protect line 5 times on `unclear`; sim PUB-01/03/09 re-asked the full question 6-10 times. Confidence medium-high (one judged failure, several near-misses).

6. **Raise or re-route the identity turn cap.** `max_turns=16 -> fin_declined` is 3 turns from firing in sim PUB-03. Either raise to 40 for both identity nodes, or route the cap to a code node that closes `not_verified/failed` with the app/branch line when `wrong_answers_left == 0`, else `declined`. Evidence: sim PUB-03 used 13 turns; sim PUB-09 9. Confidence medium (no observed failure, but the arithmetic is plain).

7. **Do not restate the floor amount in the attempt that follows a floor refusal**: `Could you pay that on Friday 16 October 2026?` (or `the minimum` without the figure). Also consider making the first floor offer undated (`Can you pay 637.50 today? The rest stays overdue.`) so the follow-up is a date question rather than a second offer. Evidence: style2-a PUB-06 failed exactly on "offered twice (12 and 14)"; sim PUB-06 said 637.50 four times and passed. Confidence medium; the brief allows the current wording, the judge did not.

8. **Treat a truncated `no` as `unclear`.** In `reply_desc`, add: "a message that stops right after 'I can't', 'I can't do it that soon because', 'I'm sorry but' is unclear, not no". Raise `min_confidence` for `no`, `too_much` and `evasive` to about 0.75 (the fatal `no` was 0.59; genuine `no`s were 1.0 everywhere). Evidence: style2-a PUB-06 22.9 (fail), style2-b PUB-13 15.2 (attempt burnt on an early-band product). Confidence medium; two instances, none on the newest agent yet.

9. **Bound the "Can you do that?" run and keep the question anchored.** Keep `_short`, but every 4th consecutive unclear restate the proposal without figures (`Could you pay on the date I proposed?`) and after about 12 consecutive unclear turns ask `Are you still there?` once. Do not close on unclear in `offer`/`ask_today` (the cases need the answer). Evidence: sim PUB-04 x20 (passed), hang-ups after 12 and 67 identical lines. The threshold is a guess; one data point each side.

10. **Protect the `decide` code node from the 131 072-byte payload limit.** Stop passing the growing transcript / `variable_table` (and the accumulating `id_heard`) into code nodes; keep only the negotiation state. Evidence: exp-wait2 PUB-12 from 134.8 (every `decide` call failed, engine re-said stale `neg_say`). Any 60+ turn call will hit this. Confidence high for the mechanism, low for how soon a normal call reaches it (only the pathological one did).

11. **Only accumulate `id_heard` when `id_last` is non-empty**, and reset it after each `verify_identity` call. Evidence: style2-a PUB-03 11.3 (`twelve July nineteen ninety one July 19`), style2-a PUB-04 38.4 (leak text in the buffer). No wrong answer was burnt by it in these 26 calls; thin.

12. **Run Brief B (PUB-11..13) on the newest agent with `--style talkative`, three reps each, before claiming 3/3.** Nothing on record tests `60d8c96` against the multi-product paths, and items 1-3 are all Brief B or confirm-phase problems that Brief A's single product hides.

Not recommended: changing the closing-on-fragment behaviour (2.7), or any `waitSeconds` work (the `exp-wait2` run shows the cut is not turn-taking: exp-wait2 PUB-12 had 45 cuts in 80 turns at 2.0 s).

## 8. What a 3/3 pass needs, by case

| case | newest-agent evidence | remaining exposure with this persona |
|---|---|---|
| PUB-01 | 1/1 | identity cap (6), re-ask count (5) |
| PUB-02 | 1/1 | none seen |
| PUB-03 | 1/1 | identity cap (6): 13 of 16 turns used |
| PUB-04 | 1/1 | 20x "Can you do that?" (9); hang-up risk |
| PUB-05 | 1/1 | fragment-hardship (2.6), protect line x5 (5) |
| PUB-06 | 1/1 | amount repeated in confirm (3, 7) |
| PUB-07 | 1/1 | read-back x4 in confirm (3) |
| PUB-08 | 1/1 | none seen |
| PUB-09 | 1/1 | identity re-asks x7 (5) |
| PUB-10 | 1/1 | none beyond confirm repeats (3) |
| PUB-11 | 0/2 (old agent) | decline misread is fixed; identity re-ask count (5); untested on new agent |
| PUB-12 | 0/2 (old agent) | all-products bug (1), confirm loop (2), post-cut re-ask (5); untested on new agent |
| PUB-13 | 1/2 (old agent) | confirm loop on cuts (3); untested on new agent |
