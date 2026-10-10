# Baseline: public A/B, every customer 3/3 (10 Oct 2026, ~17:20 UTC)

The first version of the generated workflow that passed every public case in all three runs.
Kept so later changes can be compared with it and, if needed, rolled back.

| Slot | Spec | Workflow id | Agent id | Published version (id) | Result file | Simulation | Result |
|---|---|---|---|---|---|---|---|
| gen-a | `SPEC_A` in `tests/hack/spec_fixtures.py` (hand-written) | `9e97e47e-a7d7-4828-9454-9d31a1d9b159` | `46c83457-2a7b-4e68-a71a-ff0013b8a42e` | 5 (`e13f3dcc-4b36-451f-b8ea-69e4a009ad93`) | `gen-a-3.json` | `9a3f4e23-7e5b-4886-8946-b568d0e3d5f3` | Brief A 10/10, each 3/3 |
| gen-b | `SPEC_B` in `tests/hack/spec_fixtures.py` (hand-written) | `e38d785f-aa3a-4225-9859-81c3d78c3231` | `1a045612-bd1a-46a8-acc9-8cb4b4bb392e` | 3 (`30696c50-cf1f-45a6-a895-332e65a910b8`) | `gen-b-3.json` | `9e55b684-b33c-4df9-bf91-2dec5ad4b47c` | Brief B 3/3, each 3/3 |

- `gen-a.workflow.json`, `gen-b.workflow.json`: `freya.export_workflow` of exactly those published versions,
  taken right after the runs (nothing was saved to these slots in between).
- The workflows were made with `builder/tools/deploy_spec.py A|B` from the code of this commit's parent working
  tree **before** the later changes in this commit (spec-driven `tool_fields`, `product_fields`, callback days,
  call-variable names). Regenerating today gives a slightly different workflow; the exports are the reference.
- Not measured here: a workflow built by `builder/build.py` from the model-extracted spec, the conversation
  styles of `run_cases.py --style`, other call dates.
