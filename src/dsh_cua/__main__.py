"""Let `python -m dsh_cua` start the stdio MCP server.

Why this exists: the console script (`dsh-cua-server`) is only invocable when the
interpreter's Scripts directory is on PATH, and that is not guaranteed — measured on a
stock python.org 3.12 install, neither the user PATH nor the machine PATH contained
`%LOCALAPPDATA%\\Programs\\Python\\Python312\\Scripts`, so `pip install dsh-cua` followed
by `dsh-cua-server` fails with "command not found" while the package itself is installed
correctly.

`python -m` needs no PATH entry and no console script, so it is the invocation that
always works. MCP clients can use it directly:

    {"mcpServers": {"win32": {
        "command": "python", "args": ["-m", "dsh_cua"]}}}
"""
from .server import main

if __name__ == "__main__":
    main()
