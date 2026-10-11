from __future__ import annotations

import json
import re
import threading
import unittest
import urllib.error
import urllib.request
from decimal import Decimal
from http.client import RemoteDisconnected
from types import SimpleNamespace

from .bank import Bank, make_server
from .evaluate import assess
from .fixtures import contract
from .runner import baseline_system, rebind_workflow, review


class LabTests(unittest.TestCase):
    def setUp(self):
        self.bank = Bank()

    def session(self, case, verify=True):
        opened = self.bank.open(case)
        sid = opened["session_id"]
        fixture = self.bank.fixtures[case]
        code = fixture["brief"]
        if verify:
            if code in "IJ":
                tool = "verify_policyholder" if code == "I" else "verify_customer"
                self.bank.call(tool, {"session_id": sid, **fixture["identity"]})
            else:
                for field, answer in fixture["identity"].items():
                    body = (
                        {"session_ref": sid, "challenge": field, "response": answer}
                        if code == "E"
                        else {"customer_id": sid, "field": field, "answer": answer}
                    )
                    self.bank.call("verify_identity", body)
        return sid

    def test_every_fixture_and_contract_is_materialized(self):
        self.assertEqual(len(self.bank.fixtures), 94)
        for cid, f in self.bank.fixtures.items():
            with self.subTest(case=cid):
                self.assertTrue(f["identity"])
                self.assertTrue(contract(f["brief"], "http://localhost/v1")["paths"])
                opened = self.bank.open(cid)
                self.assertNotIn("expected", opened)
                self.assertNotIn("identity", opened["variables"])
                self.assertNotIn("bank_private", json.dumps(f["actor"]))
                self.assertNotIn("evaluator_private", json.dumps(f["actor"]))

    def test_identity_gate_and_cross_question_total(self):
        sid = self.session("C-03", False)
        self.assertFalse(self.bank.call("get_account", {"customer_id": sid})[1]["ok"])
        for field, answer in [("postcode", "XX99"), ("postcode", "0073"), ("birth_year", "1987")]:
            self.bank.call(
                "verify_identity", {"customer_id": sid, "field": field, "answer": answer}
            )
        result = self.bank.call(
            "verify_identity", {"customer_id": sid, "field": "birth_year", "answer": "1988"}
        )[1]
        self.assertEqual(result["error"], "IDENTITY_LOCKED")
        self.assertEqual(self.bank.ledger(sid)["wrong"], 2)

    def test_e_nested_cents_and_definitive_rejection(self):
        sid = self.session("E-02")
        account = self.bank.call("get_account", {"session_ref": sid})[1]
        self.assertEqual(account["payload"]["assets"][0]["arrears_minor"], 90000)
        status, reply = self.bank.call(
            "create_promise",
            {
                "session_ref": sid,
                "asset_ref": "asset-A",
                "amount_minor": 90000,
                "due_on": "2026-10-14",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(reply["status"], "REJECTED")
        self.assertFalse(reply["payload"]["accepted"])
        self.assertEqual(self.bank.ledger(sid)["promises"], [])

    def test_retry_records_only_one_commit(self):
        sid = self.session("D-04")
        body = {"customer_id": sid, "product_id": "asset-A", "amount": 900, "date": "2026-10-14"}
        self.assertEqual(
            [self.bank.call("create_promise", body)[1]["ok"] for _ in range(3)],
            [False, False, True],
        )
        self.bank.call("close_call", {"customer_id": sid, "outcome": "D_PTP"})
        result = assess(self.bank.fixtures["D-04"], self.bank.ledger(sid))
        self.assertEqual(result["record_status"], "pass")
        self.assertEqual(result["overall_status"], "unassessed")

    def test_partial_commit_preserved(self):
        sid = self.session("E-07")
        for pid, amount in [("asset-A", 90000), ("asset-B", 60000), ("asset-B", 60000)]:
            self.bank.call(
                "create_promise",
                {
                    "session_ref": sid,
                    "asset_ref": pid,
                    "amount_minor": amount,
                    "due_on": "2026-10-14",
                },
            )
        self.assertEqual(len(self.bank.ledger(sid)["promises"]), 1)
        self.assertEqual(self.bank.ledger(sid)["promises"][0]["product_id"], "asset-A")

    def test_product_id_and_money_schema(self):
        sid = self.session("E-01")
        body = {
            "session_ref": sid,
            "asset_ref": "p1",
            "amount_minor": 90000,
            "due_on": "2026-10-14",
        }
        self.assertFalse(self.bank.call("create_promise", body)[1]["payload"]["accepted"])
        body.update(asset_ref="asset-A", amount_minor=90000.5)
        self.assertEqual(self.bank.call("create_promise", body)[0], 400)
        self.assertEqual(self.bank.ledger(sid)["promises"], [])

    def test_agent_violation_is_recorded_and_judged_not_silently_repaired(self):
        sid = self.session("C-06")
        self.bank.call(
            "create_promise",
            {"customer_id": sid, "product_id": "asset-A", "amount": 299.99, "date": "2026-10-14"},
        )
        self.bank.call("close_call", {"customer_id": sid, "outcome": "C_PTP"})
        result = assess(self.bank.fixtures["C-06"], self.bank.ledger(sid))
        self.assertEqual(result["overall_status"], "fail")
        self.assertIn("Promise outside product's floor/full bounds", result["failures"])

    def test_future_memo_callback_boundary(self):
        for cid, day in [("H-01", "2026-10-14"), ("H-06", "2026-11-02")]:
            sid = self.session(cid, False)
            self.bank.call("schedule_callback", {"customer_id": sid, "date": day, "time": "19:00"})
            self.bank.call(
                "close_call",
                {"customer_id": sid, "outcome": "H_NO_CONTACT", "reason": "H_CALLBACK"},
            )
            result = assess(self.bank.fixtures[cid], self.bank.ledger(sid))
            self.assertEqual(
                "Callback outside active policy window" in result["failures"], cid == "H-01"
            )

    def test_postclose_and_duplicate_close_are_failures(self):
        sid = self.session("F-08", False)
        self.bank.call("transfer_to_agent", {"customer_id": sid, "queue": "F_GENERAL"})
        self.bank.call("close_call", {"customer_id": sid, "outcome": "F_XFER", "reason": "F_HUMAN"})
        self.assertEqual(
            assess(self.bank.fixtures["F-08"], self.bank.ledger(sid))["record_status"], "pass"
        )
        self.assertFalse(
            self.bank.call("close_call", {"customer_id": sid, "outcome": "F_XFER"})[1]["ok"]
        )
        self.assertEqual(
            assess(self.bank.fixtures["F-08"], self.bank.ledger(sid))["overall_status"], "fail"
        )

    def claim(self, sid, key="k1"):
        return self.bank.call(
            "open_claim",
            {
                "session_id": sid,
                "policy_id": "pol-H",
                "incident_datetime": "2026-10-13T18:00:00+03:00",
                "location": "Dock Street",
                "description": "Parked car hit",
                "anyone_injured": False,
                "request_key": key,
            },
        )

    def test_committed_claim_with_lost_response_and_idempotent_replay(self):
        sid = self.session("I-04")
        self.assertEqual(self.claim(sid)[0], 0)
        operation = self.bank.call("get_operation", {"session_id": sid, "request_key": "k1"})[1]
        self.assertEqual(operation["state"], "SUCCEEDED")
        self.assertEqual(operation["claim_id"], "CL-102")
        self.assertTrue(self.claim(sid)[1]["ok"])
        self.assertEqual(self.bank.ledger(sid)["claims"], ["CL-102"])

    def test_unknown_commit_is_not_a_definitive_failure(self):
        sid = self.session("I-08")
        self.assertEqual(self.claim(sid)[0], 0)
        self.assertEqual(
            self.bank.call("get_operation", {"session_id": sid, "request_key": "k1"})[1]["state"],
            "UNKNOWN",
        )
        self.assertEqual(self.bank.ledger(sid)["claims"], [])

    def test_quote_version_conflict_reprices_without_commit(self):
        sid = self.session("J-02")
        body = {
            "session_id": sid,
            "policy_id": "pol-S",
            "version": 7,
            "change": {"address": "Pine Road 8"},
            "effective_at": "2026-10-14T12:00:00+03:00",
        }
        self.bank.call("quote_change", body)
        self.assertEqual(
            self.bank.call(
                "apply_change",
                {"session_id": sid, "quote_id": "Q1", "expected_version": 7, "request_key": "a"},
            )[1]["error"],
            "VERSION_CONFLICT",
        )
        body["version"] = 8
        q = self.bank.call("quote_change", body)[1]
        self.assertEqual(q["quote_id"], "Q2")
        self.assertEqual(q["delta_premium"], "40.00")
        self.assertEqual(self.bank.ledger(sid)["endorsements"], [])

    def test_quote_expiration_at_equality(self):
        sid = self.session("J-03")
        self.bank.call(
            "quote_change",
            {
                "session_id": sid,
                "policy_id": "pol-S",
                "version": 7,
                "change": {"address": "Pine Road 8"},
                "effective_at": "2026-10-14T12:00:00+03:00",
            },
        )
        self.bank.sessions[sid]["now"] = "2026-10-14T11:10:00+03:00"
        reply = self.bank.call(
            "apply_change",
            {"session_id": sid, "quote_id": "Q1", "expected_version": 7, "request_key": "a"},
        )[1]
        self.assertEqual(reply["error"], "QUOTE_EXPIRED")

    def test_sessions_are_isolated(self):
        first, second = self.session("C-01"), self.session("C-01", False)
        self.assertNotEqual(first, second)
        self.assertFalse(self.bank.call("get_account", {"customer_id": second})[1]["ok"])
        self.assertEqual(self.bank.ledger(second)["promises"], [])

    def test_total_customer_budget_is_shared_across_products(self):
        sid = self.session("R04")
        for pid, amount in [("card-r4", 300), ("loan-r4", 200), ("car-r4", 150)]:
            self.bank.call(
                "create_promise",
                {"customer_id": sid, "product_id": pid, "amount": amount, "date": "2026-10-14"},
            )
        result = assess(self.bank.fixtures["R04"], self.bank.ledger(sid))
        self.assertEqual(result["capacity"]["status"], "unaffordable")
        self.assertEqual(result["capacity"]["total"], "650.00")

    def test_pilot_uses_its_own_known_identity(self):
        self.assertEqual(self.bank.fixtures["R08"]["identity"]["policy_number"], "HM-0098")
        self.assertEqual(self.bank.fixtures["R12"]["identity"]["postcode"], "HB10")
        self.assertEqual(self.bank.fixtures["R30"]["identity"]["postcode"], "AB07")

    def test_pilot_customer_does_not_hear_the_bank_limits(self):
        # R01 "If offered 160" and R27 "If 300 or more is offered": the bank's floor in a
        # reaction rule, a number the customer has no way to know before the caller says it
        def numbers(value):
            text = json.dumps(value)
            return {Decimal(n) for n in re.findall(r"(?<![\d.])\d+(?:\.\d+)?(?![\d])", text)}

        for cid, f in self.bank.fixtures.items():
            if not cid.startswith("R"):
                continue
            card = f["actor"]["customer"]
            own = numbers(card.get("facts_known_to_customer")) | numbers(
                card.get("payment_capacity")
            )
            heard = numbers([card.get("response_rules"), card.get("boundaries")])
            for p in f["products"]:
                with self.subTest(case=cid, product=p["product_id"]):
                    floor = Decimal(str(p["floor"]))
                    self.assertTrue(floor in own or floor not in heard)

    def test_baseline_names_no_unit_the_tools_do_not_give(self):
        system = baseline_system(self.bank.fixtures["R01"], {})
        self.assertIn("add a currency", system)
        self.assertNotRegex(system.split("TOOLS:")[1], r"(?i)currency|GBP|EUR|pound|euro")

    def test_card_identifier_is_not_a_car_loan(self):
        self.assertEqual(
            [p["type"] for p in self.bank.fixtures["R04"]["products"]],
            ["credit_card", "personal_loan", "car_loan"],
        )

    def test_judge_cannot_pass_without_covering_required_checks(self):
        sid = self.session("F-08", False)
        self.bank.call("transfer_to_agent", {"customer_id": sid, "queue": "F_GENERAL"})
        self.bank.call("close_call", {"customer_id": sid, "outcome": "F_XFER", "reason": "F_HUMAN"})
        fixture = self.bank.fixtures["F-08"]
        ledger = self.bank.ledger(sid)
        result = assess(fixture, ledger)
        model = SimpleNamespace(
            trace=SimpleNamespace(path=None),
            call=lambda *a: json.dumps(
                {
                    "passed": True,
                    "customer_valid": True,
                    "checks": [
                        {
                            "check": "Everything is good",
                            "passed": True,
                            "evidence": "Tool sequence 1",
                        }
                    ],
                }
            ),
        )
        review(model, fixture, [], ledger, result, "fake")
        self.assertEqual(result["overall_status"], "fail")

    def test_pilot_cannot_pass_without_observed_branch_and_records(self):
        fixture = self.bank.fixtures["R04"]
        sid = self.session("R04")
        for pid, amount in [("card-r4", 300), ("loan-r4", 200)]:
            self.bank.call(
                "create_promise",
                {"customer_id": sid, "product_id": pid, "amount": amount, "date": "2026-10-14"},
            )
        self.bank.call(
            "add_note", {"customer_id": sid, "product_id": "car-r4", "reason": "F_REFUSED"}
        )
        self.bank.call("close_call", {"customer_id": sid, "outcome": "F_PTP"})
        ledger = self.bank.ledger(sid)
        result = assess(fixture, ledger)
        audit = {
            "passed": True,
            "customer_valid": True,
            "branch_index": None,
            "checks": [
                {"check": c, "passed": True, "evidence": "Transcript"}
                for c in fixture["evaluator_private"]["required_controls"]
            ],
        }
        model = SimpleNamespace(trace=SimpleNamespace(path=None), call=lambda *a: json.dumps(audit))
        review(model, fixture, [], ledger, result, "fake")
        self.assertEqual(result["overall_status"], "fail")

    def test_rebind_actual_operation_name_with_api_handler(self):
        w = {
            "function": {
                "name": "get_account",
                "handler": "__function__:api_call",
                "parameters": {
                    "properties": {"url": {"default": "https://c-fixture.invalid/v1/get_account"}}
                },
            }
        }
        self.assertIn(
            "https://test.example/v1/get_account",
            json.dumps(rebind_workflow(w, "https://test.example")),
        )

    def test_workflow_rebind_copies_input(self):
        workflow = {
            "nodes": {
                "n": {
                    "pre_actions": [
                        {
                            "function": {
                                "name": "api_call",
                                "parameters": {
                                    "properties": {
                                        "url": {
                                            "default": "https://c-fixture.invalid/v1/get_account"
                                        }
                                    }
                                },
                            }
                        }
                    ]
                }
            }
        }
        result = rebind_workflow(workflow, "https://test.example")
        self.assertIn("https://test.example/v1/get_account", json.dumps(result))
        self.assertIn(".invalid", json.dumps(workflow))
        with self.assertRaises(ValueError):
            rebind_workflow({"nodes": {}}, "https://test.example")


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.bank = Bank()
        self.server = make_server(self.bank, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, path, body=None, admin=False):
        headers = {"Content-Type": "application/json"}
        if admin:
            headers["Authorization"] = "Bearer " + self.server.admin_token
        req = urllib.request.Request(
            self.url + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=3) as response:
            return json.load(response)

    def test_admin_separation_and_public_tools(self):
        with self.assertRaises(urllib.error.HTTPError) as exc:
            self.request("/admin/sessions", {"case_id": "C-01"})
        self.assertEqual(exc.exception.code, 401)
        opened = self.request("/admin/sessions", {"case_id": "C-01"}, True)
        sid = opened["session_id"]
        reply = self.request("/v1/get_account", {"customer_id": sid})
        self.assertEqual(reply["error"], "UNVERIFIED")
        with self.assertRaises(urllib.error.HTTPError):
            self.request("/admin/ledger/" + sid)
        self.assertEqual(self.request("/admin/ledger/" + sid, admin=True)["promises"], [])
        self.assertNotIn("fixture", self.request("/admin/ledger/" + sid, admin=True))

    def test_http_response_is_really_lost_after_commit(self):
        opened = self.request("/admin/sessions", {"case_id": "I-04"}, True)
        sid = opened["session_id"]
        self.request(
            "/v1/verify_policyholder",
            {"session_id": sid, "policy_number": "HM-0042", "postcode": "HB07"},
        )
        with self.assertRaises((RemoteDisconnected, urllib.error.URLError)):
            self.request(
                "/v1/open_claim",
                {
                    "session_id": sid,
                    "policy_id": "pol-H",
                    "incident_datetime": "2026-10-13T18:00:00+03:00",
                    "location": "Dock Street",
                    "description": "Parked car hit",
                    "anyone_injured": False,
                    "request_key": "k1",
                },
            )
        self.assertEqual(
            self.request("/v1/get_operation", {"session_id": sid, "request_key": "k1"})["state"],
            "SUCCEEDED",
        )
        self.assertEqual(self.request("/admin/ledger/" + sid, admin=True)["claims"], ["CL-102"])


if __name__ == "__main__":
    unittest.main()
