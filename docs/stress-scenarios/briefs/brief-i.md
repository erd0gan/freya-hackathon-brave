# Brief I: Harbor Mutual — first notice of loss

Synthetic extension brief, not an official final task. This requires a claims workflow and new tools; the current seven-tool collections contract is insufficient. English calls; use call variable now with explicit offset. Never use real time. All data are synthetic.

## Scope and caller

Inbound car-insurance claims only. Introduce Harbor Mutual's digital assistant and recording. Verify policy_number and postcode with verify_policyholder before any policy or claim disclosure/mutation. A spouse/broker has no authority unless verify_policyholder explicitly returns authorized=true for that caller. Two wrong complete checks total -> close_call UNVERIFIED. No card PIN or payment details. An immediate emergency/injury report can trigger a generic instruction to contact local emergency services without disclosing policy data; do not diagnose or give medical instructions. Transfer injury reports to an adjuster as soon as possible, even before verification, without opening a claim.

## Policy and event eligibility

After authorization, get_policy returns policy_id, period_start, period_end, status_now, incident_coverage_status, timezone, rental_covered and rental_daily_limit. Bounds are inclusive instants with offsets. Incident must fall within the period and incident_coverage_status must be covered; status_now=lapsed alone does NOT bar an incident within historical cover. If incident_coverage_status=unknown, do not decide coverage or open a claim; transfer ADJUSTER. Outside period -> explain this workflow cannot open it, offer ADJUSTER; if declined close INELIGIBLE. Never promise claim settlement.

Collect incident_datetime, location, concise description and anyone_injured (yes/no). Uncertain injury -> transfer ADJUSTER, no open_claim. Corrected facts replace prior facts; read every final value and obtain explicit consent. Consent becomes invalid after any change.

Before opening, find_claim(policy_id, incident_datetime, location) identifies a duplicate. Existing matching claim -> disclose its returned claim_id, do not open again. Different incident -> separate claim only after its own confirmation. No matching claim + eligible + no injury + explicit confirmation -> open_claim with a stable request_key for that exact confirmed incident. A repeated request_key returns the existing claim, never creates another.

## Follow-up actions

An inspection requires an open claim and a slot returned by list_inspection_slots. Offer only returned weekday slots; do not invent availability. Read the location/time, get explicit consent, then book_inspection. A SLOT_TAKEN result commits nothing: refresh slots, obtain new consent, then book once more. No slot -> offer adjuster, not a fake booking.

Rental: policy rental_covered=true AND claim open; read returned daily limit and get separate explicit rental consent. Opening a claim or accepting an inspection is not rental consent. arrange_rental may be called once per consent and uses its own stable request_key. When uncovered, say unavailable under the returned policy without inventing alternatives/prices.

## Failures and completion

open_claim timeout: do not claim success. Query get_operation(request_key); SUCCEEDED includes claim_id; FAILED confirms no commit; UNKNOWN means transfer ADJUSTER, no blind second write. If success, report claim_id and continue. Other mutations only succeed on ok=true. All read operations may retry once on transport failure. Failed identity service calls do not consume wrong-answer quota. No record cancellation tool exists.

Successful claim plus failed inspection remains a successful claim; never erase it or claim booking success. Use close_call exactly once and last: CLAIM_OPENED for a newly confirmed successful claim (detail inspection_unavailable when applicable); EXISTING_CLAIM for a found duplicate; INELIGIBLE after declined referral; TRANSFERRED with ADJUSTER after successful transfer; UNVERIFIED for authorization failure; SERVICE_ERROR for unhandled service failures. Transfer wins over prior claim outcome, but preserves the claim. A completed claim is not an accepted/paid claim.

## Extension tool contract (semantic design, not deployed OpenAPI)

All tools receive session_id. verify_policyholder(policy_number, postcode) -> {ok, authorized, wrong_count}; get_policy(policy_number) -> fields above; find_claim(policy_id, incident_datetime, location) -> {ok, match: claim|null}; open_claim(policy_id, incident_datetime, location, description, anyone_injured, request_key) -> {ok, claim_id}; get_operation(request_key) -> {state: SUCCEEDED|FAILED|UNKNOWN, claim_id?}; list_inspection_slots(claim_id) -> {ok, slots:[{slot_id,start,location}]}; book_inspection(claim_id,slot_id,request_key) -> {ok,booking_id?,error?}; arrange_rental(claim_id,request_key) -> {ok,rental_id?}; transfer_to_agent(queue=ADJUSTER) -> {ok}; close_call(outcome,detail?) -> {ok}. Policy money is major units to two decimals. Responses supplied in each fixture are authoritative; absent optional data cannot be invented.
