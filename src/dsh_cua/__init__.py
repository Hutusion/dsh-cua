"""dsh-cua: Windows computer-use MCP server.

Accessibility-first element actions (UIA), skyshot text trees, guarded raw input,
and a cross-session arbiter that yields to the human.

The tools only work on Windows: they drive user32/kernel32 and the UI Automation stack.
The package still imports, and the server still starts, on other platforms — so an MCP
client, or a directory that enumerates servers by running `tools/list`, can see the full
tool list. Calling one raises a clear error instead of the process dying at import.

The previous release raised here instead, which made that introspection impossible: the
server could not start off Windows at all.
"""
import sys

__version__ = "0.3.2"

WINDOWS = sys.platform == "win32"

WINDOWS_ONLY_MESSAGE = (
    "dsh-cua drives the Windows desktop through user32/kernel32 and UI Automation, so this "
    f"operation requires Windows; this process is on {sys.platform!r}."
)
