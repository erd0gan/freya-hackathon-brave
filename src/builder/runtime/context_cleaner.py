"""Block-based context cleaning (memo §13, SABER).

System/policy and the current task are never dropped. Old tool results are
compressed and marked possibly stale after any later write; recent ones stay.
"""

from __future__ import annotations

from builder.llm import Message

KEEP_RECENT_TOOL_RESULTS = 6
OLD_RESULT_CHARS = 300


def clean(messages: list[Message], write_tools: set[str]) -> list[Message]:
    tool_idx = [i for i, m in enumerate(messages) if m.role == "tool"]
    if len(tool_idx) <= KEEP_RECENT_TOOL_RESULTS:
        return messages
    last_write = max((i for i in tool_idx if messages[i].name in write_tools), default=-1)
    old = set(tool_idx[:-KEEP_RECENT_TOOL_RESULTS])
    out: list[Message] = []
    for i, m in enumerate(messages):
        if i in old and m.content and len(m.content) > OLD_RESULT_CHARS:
            stale = " [older result; data may have changed since]" if i < last_write else ""
            m = m.model_copy(update={"content": m.content[:OLD_RESULT_CHARS] + " …" + stale})
        out.append(m)
    return out
