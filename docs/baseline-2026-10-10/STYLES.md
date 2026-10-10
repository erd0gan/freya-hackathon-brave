# Conversation styles on the model-spec agents (10 Oct 2026)

`run_cases.py --style <s>`, one run each, on agents built by `builder/build.py` from the model-extracted
spec (Brief A alone; Brief A + B in one file). Results files: `kit/results/style*-*.json` (not committed).

## Round 1: agents from commit `be73311`

| Style | A | B | Failure and cause | Fix |
|---|---|---|---|---|
| pieces | 10/10 | 2/3 | PUB-12: on a partial answer the agent re-asked the whole date-of-birth question (judge: "asked again"); digit pieces joined in the wrong order ("ninety-nine", "zero-four" sent as 0499) burnt wrong answers (also seen in PUB-10, which passed with one try left) | `6b28e2f`: ask only for the missing part; `id_heard` / `id_last` extraction; the pre-hook keeps the heard order |
| asks_back | 9/10 | 3/3 | PUB-02: "Why do you need to speak with her?" read as a refusal, closed without a callback | `6b28e2f`: third-party questions are their own class |
| corrects | 10/10 | 3/3 | | |

## Round 2: agents from commit `6b28e2f`

| Style | A | B | Failure and cause | Fix |
|---|---|---|---|---|
| pieces | 10/10 | 3/3 | PUB-07 passed, but the year came as "19" then "75" and "19" was sent as 2019 (one wrong answer burnt) | `4c33239`: a lone century is sent unreadable on purpose (the practice bank does not count it) and the model asks for the rest |
| asks_back | 10/10 | 2/3 | PUB-12: an add_note request hung past the 12 s tool timeout and reached the bank after close_call ("the call is already closed") | `fc6c4a2`: writes get a 25 s timeout, no automatic retry |
| talkative | 7/10 | 0/3 | The simulated customer's long messages arrive cut off ("I haven'", "on the thirti"), often before the answer. Then: small talk read as declining the identity questions (closed `not_verified/declined`); the full floor offer repeated after small talk (judge: "minimum offered twice") | `60d8c96`: short re-ask without amounts; declining needs a clear refusal |
| in_words | 10/10 | 3/3 | | |

Cut-off customer messages: a test with `agent_config.ttsConfig.startSpeakingPlan.waitSeconds` raised from
0.5 to 2.0 still showed cut messages (t=8 customer, t=8 agent), so the cut is not the agent taking the turn
early. It is outside the workflow; the agent's job is to stay in the call until the answer arrives.
