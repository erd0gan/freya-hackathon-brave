"""Hand-written specs for the public Brief A and Brief B: what the spec extractor must produce.
Test data only. The builder never imports this file."""

import copy

from test_engine import CFG, CFG_A

LINES = {
    "opening": "Hello, this is the digital assistant of Freya Bank, and we are recording this call.",
    "who_question": "Am I speaking with {{title}} {{last_name}}?",
    "ai_answer": "I am Freya Bank's digital assistant.",
    "scam_answer": "You can verify this call by phoning Freya Bank's customer service number.",
    "deceased": "I am sorry for your loss.",
    "identity_intro": "Before we continue, I need to ask you two security questions.",
    "identity_why": "These questions protect your account.",
    "identity_failed": "I cannot continue. Please use the mobile app or visit a branch.",
    "no_info": "I do not have that information.",
    "callback_booked": "Thank you, we will call back then.",
    "goodbye": "Thank you for your time. Goodbye.",
}

SPEC_A = {
    "cfg": CFG_A,
    "bank_name": "Freya Bank",
    "lines": LINES,
    "forbidden_before_verify": ["collections", "debt", "payment", "credit card", "loan"],
    "identity": {
        "questions": [
            {"field": "date_of_birth", "ask": "What is your date of birth?", "kind": "date"},
            {
                "field": "phone_last4",
                "ask": "What are the last four digits of the phone number we have on file?",
                "kind": "digits",
            },
        ],
        "swap_hint": "day_month_swapped",
        "swap_instruction": "Ask the customer to say the month of their date of birth as a word.",
    },
    "outcomes": {
        "unclear": ["not_reached", "unclear"],
        "callback": ["not_reached", "callback"],
        "no_callback": ["not_reached", "no_callback"],
        "wrong_number": ["not_reached", "wrong_number"],
        "deceased": ["not_reached", "deceased"],
        "failed": ["not_verified", "failed"],
        "declined": ["not_verified", "declined"],
    },
    "callback_reasks": 1,
}

SPEC_B = copy.deepcopy(SPEC_A)
SPEC_B["cfg"] = CFG
