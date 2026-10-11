"""Offline calibration of packet boundaries and budget checks, not phone-agent tests."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from customer_lab import (
    actor_packet,
    capacity_assessment,
    consent_findings,
    load_data,
    main,
    money,
    validate_pack,
)


class CustomerLabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profiles, cls.encounters = load_data()

    def packet(self, rid="R03"):
        e = self.encounters[rid]
        return actor_packet(e, self.profiles[e["profile_id"]])

    def test_all_authored_packets_validate(self):
        self.assertEqual(validate_pack()["actor_packets_validated"], 30)

    def test_oracle_and_bank_changes_do_not_change_actor(self):
        original = self.encounters["R03"]
        changed = copy.deepcopy(original)
        changed["bank_private"] = {"secret": "CANARY_BANK"}
        changed["evaluator_private"] = {"expected": "CANARY_EXPECTED"}
        changed["successCriteria"] = "CANARY_JUDGE"
        p = self.profiles[original["profile_id"]]
        self.assertEqual(actor_packet(changed, p), actor_packet(original, p))

    def test_unknown_nested_metadata_is_excluded(self):
        e = copy.deepcopy(self.encounters["R03"])
        e["shared_context"]["expected"] = "SECRET"
        e["customer_private"]["extra_context"] = {"oracle": "SECRET"}
        self.assertNotIn("SECRET", json.dumps(actor_packet(e, self.profiles[e["profile_id"]])))

    def test_third_party_gets_no_customer_security(self):
        self.assertEqual(self.packet("R05")["customer"]["identity_answers"], {})

    def test_accidental_third_party_credentials_are_rejected(self):
        e = copy.deepcopy(self.encounters["R05"])
        e["customer_private"]["identity_answers"] = {"postcode": "CD09"}
        with self.assertRaises(ValueError):
            actor_packet(e, self.profiles[e["profile_id"]])

    def test_profiles_cannot_be_blindly_crossed(self):
        with self.assertRaises(ValueError):
            actor_packet(self.encounters["R03"], self.profiles["P18"])

    def test_export_does_not_mutate_fixture(self):
        packet = self.packet()
        packet["customer"]["identity_answers"]["member_code"] = "changed"
        self.assertEqual(self.encounters["R03"]["customer_private"]["identity_answers"]["member_code"], "004271")

    def assess(self, amounts, rid="R04", dates=None):
        e = self.encounters[rid]
        proposals = [{"product_id": f"p{i}", "amount": a, "date": dates[i] if dates else "2026-10-14", "currency": "GBP"}
                     for i, a in enumerate(amounts)]
        return capacity_assessment(e["customer_private"]["payment_capacity"], proposals, "2026-10-14")

    def test_exact_total_budget_is_allowed(self):
        self.assertEqual(self.assess(["300.00", "200.00"])["status"], "within_capacity")

    def test_budget_is_not_reset_per_product(self):
        self.assertEqual(self.assess(["300.00", "200.00", "150.00"])["status"], "unaffordable")

    def test_one_penny_above_budget_is_not_rounded_down(self):
        self.assertEqual(self.assess(["500.01"])["status"], "unaffordable")

    def test_money_available_tomorrow_is_not_available_today(self):
        self.assertEqual(self.assess(["300.00"], "R10")["status"], "unaffordable")
        self.assertEqual(self.assess(["300.00"], "R10", ["2026-10-15"])["status"], "within_capacity")

    def test_future_optimism_does_not_create_money(self):
        self.assertEqual(self.assess(["160.00"], "R01", ["2026-10-16"])["status"], "unaffordable")

    def test_capacity_is_cumulative_across_dates(self):
        cap = {"currency": "GBP", "available_by": [{"date": "2026-10-14", "amount": "300.00"}, {"date": "2026-10-15", "amount": "500.00"}]}
        p = [{"product_id": "a", "amount": "300.00", "date": "2026-10-14", "currency": "GBP"},
             {"product_id": "b", "amount": "300.00", "date": "2026-10-15", "currency": "GBP"}]
        self.assertEqual(capacity_assessment(cap, p, "2026-10-14")["status"], "unaffordable")

    def test_missing_capacity_is_unknown_not_yes(self):
        self.assertEqual(capacity_assessment(None, [], "2026-10-14")["status"], "unknown")

    def test_no_binary_float_nonfinite_negative_or_excess_precision(self):
        for value in (1.1, True, "NaN", "Infinity", "-1", "420.505"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                money(value)

    def test_invalid_currency_and_duplicate_commitments(self):
        cap = self.encounters["R04"]["customer_private"]["payment_capacity"]
        p = {"product_id": "a", "amount": "100.00", "date": "2026-10-14", "currency": "USD"}
        self.assertEqual(capacity_assessment(cap, [p], "2026-10-14")["status"], "invalid_input")
        p["currency"] = "GBP"
        self.assertEqual(capacity_assessment(cap, [p, p], "2026-10-14")["status"], "invalid_input")

    def test_cli_exports_only_customer_packet(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "r03.json"
            main(["export", "R03", "--out", str(path)])
            packet = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(set(packet), {"instructions", "shared_context", "profile", "customer"})
        self.assertNotIn("bank_private", packet)

    def test_duplicate_or_nonmonotone_capacity_rejected(self):
        cap = {"currency": "GBP", "available_by": [{"date": "2026-10-15", "amount": "500.00"}, {"date": "2026-10-14", "amount": "300.00"}]}
        self.assertEqual(capacity_assessment(cap, [], "2026-10-14")["status"], "invalid_input")


class AnnotatedConsentTests(unittest.TestCase):
    def setUp(self):
        self.promise = {"product_id": "card", "amount": "300.00", "date": "2026-10-15"}
        self.readback = {"kind": "readback", "revision": "r1", "promises": [self.promise]}
        self.yes = {"kind": "customer", "act": "confirms_write", "revision": "r1"}
        self.write = {"kind": "promise_attempt", **self.promise, "result": "committed"}

    def test_acknowledgement_refusal_correction_and_conditions_are_not_consent(self):
        for act in ("acknowledges", "questions_action", "refuses", "corrects", "conditional"):
            with self.subTest(act=act):
                findings = consent_findings([self.readback, {"kind": "customer", "act": act}, self.write])
                self.assertTrue(any(f["kind"] == "consent_violation" for f in findings))

    def test_explicit_bound_confirmation_allows_the_write(self):
        self.assertEqual(consent_findings([self.readback, self.yes, self.write]), [])

    def test_changed_amount_date_or_product_is_not_approved(self):
        for field, value in [("product_id", "loan"), ("date", "2026-10-16"), ("amount", "301.00")]:
            with self.subTest(field=field):
                changed = {**self.write, field: value}
                self.assertTrue(consent_findings([self.readback, self.yes, changed]))

    def test_retraction_revokes_previous_consent(self):
        self.assertTrue(consent_findings([self.readback, self.yes, {"kind": "customer", "act": "retracts"}, self.write]))

    def test_new_readback_needs_new_consent(self):
        new = {**self.readback, "revision": "r2"}
        self.assertTrue(consent_findings([self.readback, self.yes, new, self.write]))
        new_yes = {**self.yes, "revision": "r2"}
        self.assertEqual(consent_findings([self.readback, self.yes, new, new_yes, self.write]), [])

    def test_definitive_failure_allows_same_authorised_retry(self):
        failed = {**self.write, "result": "definitive_no_commit"}
        self.assertEqual(consent_findings([self.readback, self.yes, failed, self.write]), [])

    def test_uncertain_commit_or_success_does_not_allow_blind_duplicate(self):
        for result in ("unknown", "committed"):
            with self.subTest(result=result):
                first = {**self.write, "result": result}
                self.assertTrue(consent_findings([self.readback, self.yes, first, self.write]))

    def test_one_collective_consent_covers_exactly_its_two_products(self):
        second = {**self.promise, "product_id": "loan", "amount": "200.00"}
        readback = {**self.readback, "promises": [self.promise, second]}
        second_write = {"kind": "promise_attempt", **second, "result": "committed"}
        self.assertEqual(consent_findings([readback, self.yes, self.write, second_write]), [])

    def test_missing_annotations_are_not_a_pass(self):
        self.assertEqual(consent_findings([])[0]["kind"], "annotation_error")
        self.assertTrue(consent_findings([self.readback, {"kind": "customer", "act": "unlabelled"}, self.write]))


if __name__ == "__main__":
    unittest.main()
