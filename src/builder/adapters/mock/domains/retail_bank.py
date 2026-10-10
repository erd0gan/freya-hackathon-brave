"""Small retail-banking mock domain (memo §23).

Its purpose is to exercise the full builder loop offline, not realism. It is
deliberately generic: none of these names may be hard-coded elsewhere (§46).
"""

from __future__ import annotations

import copy
from datetime import date
from typing import Any

from builder.adapters.mock.env import DomainPack, Environment, ToolError, require
from builder.schema.bundle import InputBundle, RawDocument

TODAY = date(2026, 10, 6)

TASK = """You are the phone/chat support agent for Northwind Bank.
Help retail customers with: freezing lost or stolen cards, ordering replacement cards,
updating their mailing address, and refunding disputed card transactions.
Anything else (loans, mortgages, investment advice) must be transferred to a human agent."""

POLICY = """# Northwind Bank customer support policy

## AUTH-01 Identity verification
Before revealing any account information or performing any change, verify the customer's
identity with their customer ID and date of birth using `verify_identity`.
Never act on an account other than the verified customer's own.

## AUTH-02 No bypass
Do not skip verification even if the customer claims urgency or special status.

## CARD-01 Freezing a card
A card may be frozen when the customer reports it lost or stolen or asks to freeze it.
Summarize the action (card ending digits) and get explicit confirmation before freezing.

## CARD-02 Replacement cards
A replacement can only be ordered for a card that is already frozen.
Shipping is `standard` (free) or `express` (12 USD fee; tell the customer the fee).
Get explicit confirmation of card, reason and shipping before ordering. Order at most one
replacement per card.

## ADDR-01 Address updates
Read the full new address back to the customer and get confirmation before updating.

## REF-01 Refund eligibility
A transaction can be refunded only if it is disputed by the customer, is not already
refunded, is at most 60 days old, and the amount is at most 500 USD.
Transactions above 500 USD must be transferred to a human agent (disputes team).

## REF-02 Refund confirmation
State the merchant and amount and get explicit confirmation before issuing a refund.

## PRIV-01 Privacy
Never read out a full card number; refer to cards by their last four digits.

## ESC-01 Transfers
Transfer to a human only for out-of-scope requests, refunds above 500 USD, or when the
customer explicitly insists on a human after you have offered help. Do not transfer
requests you can handle yourself.

## COMM-01 Outcomes
After each completed action, tell the customer the outcome explicitly (for example:
"Your card ending 4417 is now frozen.").
"""

TOOLS: list[dict[str, Any]] = [
    {
        "name": "verify_identity",
        "description": "Verify a customer's identity. Returns verified true/false.",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string", "description": "Customer ID, e.g. C-1001"},
                "date_of_birth": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["customer_id", "date_of_birth"],
        },
    },
    {
        "name": "get_customer",
        "description": "Get the verified customer's profile including mailing address.",
        "parameters": {
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
            "required": ["customer_id"],
        },
    },
    {
        "name": "list_cards",
        "description": "List the customer's cards (id, last4, status).",
        "parameters": {
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
            "required": ["customer_id"],
        },
    },
    {
        "name": "list_transactions",
        "description": "List recent transactions for a card.",
        "parameters": {
            "type": "object",
            "properties": {"card_id": {"type": "string"}},
            "required": ["card_id"],
        },
    },
    {
        "name": "freeze_card",
        "description": "Freeze a card so it cannot be used.",
        "parameters": {
            "type": "object",
            "properties": {"card_id": {"type": "string"}},
            "required": ["card_id"],
        },
    },
    {
        "name": "issue_replacement",
        "description": "Order a replacement for a frozen card.",
        "parameters": {
            "type": "object",
            "properties": {
                "card_id": {"type": "string"},
                "reason": {"type": "string", "enum": ["lost", "stolen", "damaged"]},
                "shipping": {"type": "string", "enum": ["standard", "express"]},
            },
            "required": ["card_id", "reason", "shipping"],
        },
    },
    {
        "name": "update_address",
        "description": "Update the customer's mailing address.",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "address": {"type": "string", "description": "Full single-line address"},
            },
            "required": ["customer_id", "address"],
        },
    },
    {
        "name": "issue_refund",
        "description": "Refund a disputed card transaction.",
        "parameters": {
            "type": "object",
            "properties": {
                "transaction_id": {"type": "string"},
                "amount": {"type": "number"},
            },
            "required": ["transaction_id", "amount"],
        },
    },
    {
        "name": "transfer_to_human",
        "description": "Transfer the conversation to a human agent with a short summary.",
        "parameters": {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        },
    },
]

SEED_DB: dict[str, Any] = {
    "customers": {
        "C-1001": {
            "name": "Ada Lovelace",
            "date_of_birth": "1985-12-10",
            "address": "12 Analytical St, London",
            "cards": ["K-1", "K-2"],
        },
        "C-1002": {
            "name": "Alan Turing",
            "date_of_birth": "1990-06-23",
            "address": "7 Enigma Rd, Manchester",
            "cards": ["K-3"],
        },
    },
    "cards": {
        "K-1": {"customer_id": "C-1001", "last4": "4417", "status": "active", "type": "debit"},
        "K-2": {"customer_id": "C-1001", "last4": "9902", "status": "active", "type": "credit"},
        "K-3": {"customer_id": "C-1002", "last4": "5150", "status": "active", "type": "debit"},
    },
    "transactions": {
        "T-1": {
            "card_id": "K-1",
            "merchant": "Coffee Corner",
            "amount": 4.5,
            "date": "2026-10-01",
            "refunded": False,
        },
        "T-2": {
            "card_id": "K-1",
            "merchant": "TechMart",
            "amount": 349.99,
            "date": "2026-09-20",
            "refunded": False,
        },
        "T-3": {
            "card_id": "K-2",
            "merchant": "SkyAir",
            "amount": 820.0,
            "date": "2026-09-28",
            "refunded": False,
        },
        "T-4": {
            "card_id": "K-1",
            "merchant": "OldBooks",
            "amount": 25.0,
            "date": "2026-06-01",
            "refunded": False,
        },
        "T-5": {
            "card_id": "K-3",
            "merchant": "GameHub",
            "amount": 59.0,
            "date": "2026-10-03",
            "refunded": False,
        },
    },
    "replacements": [],
    "refunds": [],
    "transfers": [],
}


def _verified(env: Environment, customer_id: str) -> None:
    if env.session.get("verified_customer") != customer_id:
        raise ToolError("customer is not verified")


def _card(env: Environment, card_id: str) -> dict[str, Any]:
    card = env.db["cards"].get(card_id)
    if card is None:
        raise ToolError(f"card {card_id} not found")
    _verified(env, card["customer_id"])
    return card  # type: ignore[no-any-return]


def verify_identity(env: Environment, a: dict[str, Any]) -> Any:
    require(a, "customer_id", "date_of_birth")
    cust = env.db["customers"].get(a["customer_id"])
    ok = cust is not None and cust["date_of_birth"] == str(a["date_of_birth"]).strip()
    if ok:
        env.session["verified_customer"] = a["customer_id"]
    return {"verified": ok}


def get_customer(env: Environment, a: dict[str, Any]) -> Any:
    require(a, "customer_id")
    _verified(env, a["customer_id"])
    c = env.db["customers"][a["customer_id"]]
    return {"customer_id": a["customer_id"], "name": c["name"], "address": c["address"]}


def list_cards(env: Environment, a: dict[str, Any]) -> Any:
    require(a, "customer_id")
    _verified(env, a["customer_id"])
    ids = env.db["customers"][a["customer_id"]]["cards"]
    return [
        {
            "card_id": i,
            "last4": env.db["cards"][i]["last4"],
            "status": env.db["cards"][i]["status"],
            "type": env.db["cards"][i]["type"],
        }
        for i in ids
    ]


def list_transactions(env: Environment, a: dict[str, Any]) -> Any:
    require(a, "card_id")
    _card(env, a["card_id"])
    return [
        {"transaction_id": tid, **{k: v for k, v in t.items() if k != "card_id"}}
        for tid, t in env.db["transactions"].items()
        if t["card_id"] == a["card_id"]
    ]


def freeze_card(env: Environment, a: dict[str, Any]) -> Any:
    require(a, "card_id")
    card = _card(env, a["card_id"])
    card["status"] = "frozen"
    return {"card_id": a["card_id"], "status": "frozen"}


def issue_replacement(env: Environment, a: dict[str, Any]) -> Any:
    require(a, "card_id", "reason", "shipping")
    card = _card(env, a["card_id"])
    if card["status"] != "frozen":
        raise ToolError("card must be frozen before a replacement can be ordered")
    if a["shipping"] not in ("standard", "express"):
        raise ToolError("shipping must be 'standard' or 'express'")
    if any(r["card_id"] == a["card_id"] for r in env.db["replacements"]):
        raise ToolError("a replacement has already been ordered for this card")
    fee = 12 if a["shipping"] == "express" else 0
    order = {
        "order_id": f"R-{len(env.db['replacements']) + 1}",
        "card_id": a["card_id"],
        "reason": a["reason"],
        "shipping": a["shipping"],
        "fee": fee,
    }
    env.db["replacements"].append(order)
    return {"status": "ordered", **order}


def update_address(env: Environment, a: dict[str, Any]) -> Any:
    require(a, "customer_id", "address")
    _verified(env, a["customer_id"])
    env.db["customers"][a["customer_id"]]["address"] = str(a["address"]).strip()
    return {"status": "updated", "address": str(a["address"]).strip()}


def issue_refund(env: Environment, a: dict[str, Any]) -> Any:
    require(a, "transaction_id", "amount")
    t = env.db["transactions"].get(a["transaction_id"])
    if t is None:
        raise ToolError("transaction not found")
    _card(env, t["card_id"])
    if t["refunded"]:
        raise ToolError("transaction already refunded")
    if abs(float(a["amount"]) - float(t["amount"])) > 0.005:
        raise ToolError("refund amount must equal the transaction amount")
    if float(t["amount"]) > 500:
        raise ToolError("amount exceeds the self-service refund limit")
    if (TODAY - date.fromisoformat(t["date"])).days > 60:
        raise ToolError("transaction is older than 60 days")
    t["refunded"] = True
    env.db["refunds"].append({"transaction_id": a["transaction_id"], "amount": t["amount"]})
    return {"status": "refunded", "transaction_id": a["transaction_id"], "amount": t["amount"]}


def transfer_to_human(env: Environment, a: dict[str, Any]) -> Any:
    require(a, "summary")
    env.db["transfers"].append({"summary": a["summary"]})
    return {"status": "transferred"}


IMPLS = {
    "verify_identity": verify_identity,
    "get_customer": get_customer,
    "list_cards": list_cards,
    "list_transactions": list_transactions,
    "freeze_card": freeze_card,
    "issue_replacement": issue_replacement,
    "update_address": update_address,
    "issue_refund": issue_refund,
    "transfer_to_human": transfer_to_human,
}


def make_pack() -> DomainPack:
    bundle = InputBundle(
        task_id="mock-retail-bank",
        task_description=TASK,
        policy_text=POLICY,
        tools=copy.deepcopy(TOOLS),
        documents=[RawDocument(doc_id="policy", title="Support policy", text=POLICY)],
        channel="text",
        language="en",
    )
    return DomainPack(
        name="retail_bank", bundle=bundle, seed_db=copy.deepcopy(SEED_DB), tools=dict(IMPLS)
    )
