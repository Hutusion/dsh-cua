"""dsh-cua: Windows computer-use MCP server.

Accessibility-first element actions (UIA), skyshot text trees, guarded raw input,
and a cross-session arbiter that yields to the human. Windows only by design.
"""
import sys

__version__ = "0.3.0"

if sys.platform != "win32":
    raise RuntimeError(
        "dsh-cua drives the Windows desktop via user32/kernel32 and UIA. "
        f"It only runs on Windows; got platform={sys.platform!r}."
    )
