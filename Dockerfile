# Container image for MCP directory scanners (Glama and friends).
#
# Why it builds on Linux even though the tools are Windows-only:
#   * pyproject.toml declares pywinauto/comtypes with `sys_platform == "win32"`, so a Linux
#     install simply does not pull them.
#   * src/dsh_cua/server.py guards its Win32 imports, so the server still starts and answers
#     `tools/list` with every tool and its full JSON schema.
#   * Only an actual tool CALL needs Windows; it raises a clear "requires Windows" error
#     rather than taking the process down.
# Glama's stated bar is "the server to start and respond to introspection requests", which is
# exactly what this image does. Measured on Ubuntu 24.04 / Python 3.12 before writing this.
FROM python:3.12-slim

WORKDIR /app

# Build from the checkout rather than pinning a released version, so the image can never
# describe a different revision than the repository does.
COPY . /app
RUN pip install --no-cache-dir .

# MCP over stdio: JSON-RPC in on stdin, out on stdout. The process must not print anything
# else to stdout, and it needs stdin to stay open — do not wrap the CMD in anything that
# closes it.
CMD ["python", "-m", "dsh_cua"]
