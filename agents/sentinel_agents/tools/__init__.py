"""Tool registry — imports all tools so the agent can discover them.

T2.2: SRE allow-listed tools (kubectl, PromQL, LogQL).
T2.4: RAG search.
T3.2: Security tools (trivy, CVE lookup, Falco, Tetragon).
T3.3: Cost tool (kube resource usage).
T3.4: RAG evidence tool (ranked evidence + citations).

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

# T3.2: Security Agent tools
from sentinel_agents.tools import trivy_scan  # noqa: F401
from sentinel_agents.tools import cve_lookup  # noqa: F401
from sentinel_agents.tools import falco_events  # noqa: F401
from sentinel_agents.tools import tetragon_events  # noqa: F401

# T3.3: Cost Agent tool
from sentinel_agents.tools import kube_resource_usage  # noqa: F401

# T3.4: RAG Agent tool
from sentinel_agents.tools import rag_evidence  # noqa: F401

ALLOWED_TOOLS = get_all_tools()
"""The complete list of registered, allow-listed tools."""

__all__ = ["ALLOWED_TOOLS", "get_all_tools", "get_tool_names"]
