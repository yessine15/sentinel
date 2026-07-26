"""Entry point for the SRE agent — ``python -m sentinel_agents.run``.

Usage::

    python -m sentinel_agents.run "How is my cluster doing?"
    python -m sentinel_agents.run              # uses a built-in question
"""

from __future__ import annotations

import sys

from langchain_core.messages import HumanMessage

from sentinel_agents.graph import AgentState, graph


def run(question: str) -> str:
    """Run one chat turn through the agent graph and return the final answer."""
    initial_state: AgentState = {
        "messages": [HumanMessage(content=question)],
        "tool_calls": [],
        "scratchpad": {},
    }

    print(f"\n🤖 SRE Agent\n{'─' * 60}")
    print(f"Q: {question}\n")

    result = graph.invoke(initial_state)

    # The last message should be the final AI response
    messages = result.get("messages", [])
    if messages:
        final = messages[-1]
        answer = final.content if hasattr(final, "content") else str(final)
        print(f"A: {answer}")
        print(f"{'─' * 60}\n")
        return answer

    print("(no response)\n")
    return ""


def main() -> None:
    question = sys.argv[1] if len(sys.argv) > 1 else "How is my cluster doing right now?"
    run(question)


if __name__ == "__main__":
    main()
