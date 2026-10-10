# Northwind Credit: material for the arrears calls

Northwind Credit is fictional. This file is test material written by the builder team to check that the builder reads raw bank material. It is not a hackathon brief.

---

## 1. Mail

**From:** Priya Shah, Head of Recoveries
**To:** Contact-centre automation team
**Date:** 18 September 2026
**Subject:** Outbound arrears calls: what the assistant has to do

Hi all,

Below is what the new assistant must do on our outbound arrears calls. The policy extract is attached and wins where I am vague.

Every call comes with `customer_id`, `full_name` and `now` (the date and time of the call, ISO format; use it for every date rule, never the system clock).

Start every call with exactly: "Good day, this is Northwind Credit's automated assistant, and this call is being monitored." Then ask "Is this {full_name}?". Only a clear yes or the person's own name confirms. If it is still unclear after asking twice, end the call (code list: NO_CONTACT / UNCLEAR). Say nothing about money, arrears or an account until security is passed, not even to family members.

If somebody else answers, don't say why we call. Ask when the customer can be reached and book a callback (today or one of the next two business days, 09:00 to 17:30, after `now`). If what they offer is outside that, ask one more time; if it is still outside, close without a callback. Wrong number and deceased customers are closed with their codes; for a death say "I am very sorry to hear that." and nothing else.

Security: first the postcode of the registered address, then the year of birth. One question at a time. Send each answer to `verify_identity`. If the customer refuses, explain once that it protects their account; a second refusal closes the call (NO_ID / REFUSED). When the check fails, tell them to call us back on the number on their statement (NO_ID / FAILED).

After security, call `get_account`, tell the customer we are calling about arrears on their account, name each account and say how much is in arrears and for how many days. Then ask "Are you able to clear the arrears today?" (with more than one account: "Are you able to clear all of these today?").

Thanks,
Priya

---

## 2. Policy extract: Arrears Handling Policy v4 (effective 1 July 2026)

**4.1 Calendar.** Business days are Monday to Friday except company holidays: 2026-10-12, 2026-11-02, 2026-12-25.

**4.2 Stages.**

| Days in arrears | Stage | Latest promise date | Persuasion attempts |
|---|---|---|---|
| 1 to 14 | stage 1 | 10th business day after today | 1 |
| 15 to 60 | stage 2 | 5th business day after today | 3 |

**4.3 Minimum.** The minimum acceptable amount is `min_due` for an overdraft or a store card and `monthly_payment` for an auto loan. Offer it at most once when the customer says the full amount is too much, and say that the remaining arrears stay outstanding.

**4.4 Attempts.** Attempt 1: the full arrears on the third business day after today. Attempt 2: the minimum on the date of attempt 1. Attempt 3: the minimum on the latest promise date. A vague date ("end of the month") is answered by asking once for the exact day, then proposing the latest promise date.

**4.5 Hardship.** A customer who mentions illness, bereavement or redundancy is transferred straight away to the hardship team (`transfer_to_agent`, team HARDSHIP_TEAM). No persuasion and no question first.

**4.6 People.** A customer who asks for a person is transferred at once to the service team (SERVICE). A request for a payment plan goes to the plans team (PLANS).

**4.7 Confirmation.** Read every promise back and ask "Is that correct?". Record each one with `create_promise` after a clear yes. If the service fails, try again; give up after three failed calls in total and tell the customer.  After a recorded promise say "A confirmation letter is on its way."

**4.8 Notes.** Every account that ends without a promise gets `add_note` with its code, also when the call is transferred. If no promise was recorded, the close reason is the code of the most serious note: DISPUTE before HARDSHIP before SYS_ERR before DECLINED.

**4.9** If the customer says twice that they do not want to talk, close the call.

---

## 3. Memo

**From:** Priya Shah
**Date:** 5 October 2026
**Subject:** Changes to Arrears Handling Policy v4, effective immediately

1. Stage 2 now has 2 persuasion attempts, not 3. Drop attempt 2 of 4.4 (the minimum on the attempt-1 date); the minimum on the latest promise date becomes attempt 2.
2. 2026-10-30 is a company holiday.
3. Callbacks end at 17:00, not 17:30.

---

## 4. Code list: close-out codes for `close_call`

| result | reason | meaning |
|---|---|---|
| PTP | | at least one promise recorded |
| PAID | | customer says the arrears are already paid |
| XFER | HARDSHIP, PLAN, AGENT | transferred: hardship team, plans team, a person |
| NO_PTP | DECLINED, HARDSHIP, DISPUTE, SYS_ERR | verified, no promise |
| NO_ID | FAILED, REFUSED | security not passed |
| NO_CONTACT | CALLBACK, NO_CALLBACK, WRONG_NUMBER, DECEASED, UNCLEAR | customer not reached |

Note codes for `add_note`: DECLINED, HARDSHIP, DISPUTE, PAID, SYS_ERR.
