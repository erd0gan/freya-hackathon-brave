from __future__ import annotations

from builder.knowledge.chunk import chunk_document
from builder.knowledge.doc_shaper import shaped_documents
from builder.knowledge.retriever import Retriever
from builder.llm import Message
from builder.runtime.context_cleaner import clean
from builder.schema.bundle import DomainSpec
from builder.schema.knowledge import DocumentRecord, KnowledgeSpec
from builder.schema.task import TaskSpec

DOCS = [
    DocumentRecord(
        doc_id="fees",
        title="Fees",
        text="# Fees\n## Express shipping\nExpress "
        "replacement shipping costs 12 USD and arrives in 2 days.\n## Standard\nFree.",
        links=["limits"],
    ),
    DocumentRecord(
        doc_id="limits",
        title="Limits",
        text="# Refund limits\nRefunds above 500 USD require the disputes team.",
    ),
    DocumentRecord(
        doc_id="tr", title="Kart", text="# Kart iadesi\nİade işlemi için müşteri onayı gerekir."
    ),
]


def test_chunk_heading_path():
    ch = chunk_document(DOCS[0])
    assert ch[0].heading_path == ["Fees", "Express shipping"]


def test_bm25_multi_query_and_expansion():
    r = Retriever(KnowledgeSpec(documents=DOCS))
    hits = r.search(["how much is express delivery", "shipping fee"], k=1)
    assert hits[0].doc_id == "fees"
    assert any(h.doc_id == "limits" for h in hits)  # cross-document expansion
    assert r.search(["iade onayi"], k=1, expand=False)[0].doc_id == "tr"


def test_doc_shaper_adds_rules_doc():
    from builder.schema.policy import PolicyRule, PolicySpec

    d = DomainSpec(
        task=TaskSpec(objective="x"),
        knowledge=KnowledgeSpec(documents=DOCS),
        policy=PolicySpec(rules=[PolicyRule(id="R-1", description="d", source_refs=["p#R-1"])]),
    )
    docs = shaped_documents(d)
    assert docs[-1]["filename"] == "compiled_rules.md" and "[R-1]" in docs[-1]["content"]
    assert docs[0]["content"].startswith("# Fees > Fees > Express shipping")


def test_context_cleaner_marks_stale_results():
    msgs = [Message(role="system", content="S")]
    for i in range(8):
        msgs.append(Message(role="tool", name="read", content="x" * 500 + str(i)))
    msgs.append(Message(role="tool", name="write", content="ok"))
    out = clean(msgs, {"write"})
    assert out[0].content == "S"
    assert out[1].content.endswith("data may have changed since]")
    assert out[-2].content == msgs[-2].content  # recent kept verbatim
