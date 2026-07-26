"""Tool registry — imports all tools so the agent can discover them.

Usage::

    from sentinel_agents.tools import ALLOWED_TOOLS
    llm.bind_tools(ALLOWED_TOOLS)
"""

from sentinel_agents.tools.base import get_all_tools, get_tool_names

# Import each tool module so its @tool decorated functions register themselves.
from sentinel_agents.tools import kubectl_get  # noqa: F401
from sentinel_agents.tools import kubectl_describe  # noqa: F401
from sentinel_agents.tools import promql_query  # noqa: F401
from sentinel_agents.tools import logql_query  # noqa: F401
from sentinel_agents.tools import rag_search  # noqa: F401

ALLOWED_TOOLS = get_all_tools()
"""The complete list of registered, allow-listed tools."""

__all__ = ["ALLOWED_TOOLS", "get_all_tools", "get_tool_names"]
