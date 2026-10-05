"""AI Agent Tools package.

Importing this package registers all tools into tool_registry.
Any module that needs the full tool set should do:

    from backend.app.tools import tool_registry
"""

# Importing each tool module triggers their tool_registry.register() calls.
from backend.app.tools import (  # noqa: F401
    stock_tools,
    customer_tools,
    ledger_tools,
    order_tools,
    reporting_tools,
    management_tools,
    reminder_tools,
)
from backend.app.tools.base import BaseTool, ToolInput, ToolResult, ToolRegistry, tool_registry

__all__ = [
    "BaseTool",
    "ToolInput",
    "ToolResult",
    "ToolRegistry",
    "tool_registry",
]
