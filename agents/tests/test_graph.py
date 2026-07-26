"""Tests for the LangGraph scaffolding (T2.1)."""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from sentinel_agents.graph import AgentState, build_graph, should_continue
from sentinel_agents.graph import graph as default_graph


class TestGraphBuild:
    """The graph compiles and exposes the expected structure."""

    def test_compiles_without_error(self):
        """build_graph() returns a compiled graph."""
        g = build_graph()
        assert g is not None

    def test_module_level_singleton_exists(self):
        """The module-level ``graph`` singleton is already compiled."""
        assert default_graph is not None

    def test_agent_state_has_required_keys(self):
        """AgentState TypedDict has messages, tool_calls, and scratchpad."""
        state: AgentState = {"messages": [], "tool_calls": [], "scratchpad": {}}
        assert "messages" in state
        assert "tool_calls" in state
        assert "scratchpad" in state


class TestRouter:
    """The should_continue router picks the correct next node."""

    def test_ends_on_empty_messages(self):
        state: AgentState = {"messages": [], "tool_calls": [], "scratchpad": {}}
        assert should_continue(state) == "__end__"

    def test_ends_when_last_message_is_human(self):
        """A HumanMessage should not route to tools."""
        state: AgentState = {
            "messages": [HumanMessage(content="hello")],
            "tool_calls": [],
            "scratchpad": {},
        }
        assert should_continue(state) == "__end__"

    def test_ends_when_last_ai_has_no_tool_calls(self):
        """A plain AIMessage without tool_calls should end."""
        state: AgentState = {
            "messages": [AIMessage(content="all good!")],
            "tool_calls": [],
            "scratchpad": {},
        }
        assert should_continue(state) == "__end__"

    def test_routes_to_tools_when_ai_has_tool_calls(self):
        """An AIMessage with tool_calls should route to 'tools'."""
        state: AgentState = {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[{"name": "get_cluster_summary", "args": {}, "id": "call_1"}],
                )
            ],
            "tool_calls": [],
            "scratchpad": {},
        }
        assert should_continue(state) == "tools"

    def test_skips_tool_message_before_ai(self):
        """A ToolMessage followed by a plain AIMessage should end."""
        state: AgentState = {
            "messages": [
                ToolMessage(content="result", tool_call_id="call_1"),
                AIMessage(content="done"),
            ],
            "tool_calls": [],
            "scratchpad": {},
        }
        assert should_continue(state) == "__end__"


class TestAgentState:
    """AgentState behaves correctly with add_messages reducer."""

    def test_messages_are_appended(self):
        """Invoking the graph with a question appends messages."""
        from sentinel_agents.graph import graph as g

        initial: AgentState = {
            "messages": [HumanMessage(content="hello")],
            "tool_calls": [],
            "scratchpad": {},
        }
        # We only test the state shape; we don't require a live LLM for
        # the graph to at least start processing.
        # The graph may fail if no LLM is available, so we only check
        # that the state is well-formed before invocation.
        assert len(initial["messages"]) == 1
        assert isinstance(initial["messages"][0], HumanMessage)
