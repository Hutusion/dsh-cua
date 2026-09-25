# dsh-cua

[![ci](https://github.com/Hutusion/dsh-cua/actions/workflows/ci.yml/badge.svg)](https://github.com/Hutusion/dsh-cua/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/dsh-cua)](https://pypi.org/project/dsh-cua/)
[![Hutusion/dsh-cua MCP server](https://glama.ai/mcp/servers/Hutusion/dsh-cua/badges/score.svg)](https://glama.ai/mcp/servers/Hutusion/dsh-cua)

<!-- mcp-name: io.github.Hutusion/dsh-cua -->

**English** · [中文](README.zh-CN.md)

An MCP server + agent skill for computer use on Windows: **accessibility element actions come
first and screenshots are only the fallback**. It ships a cross-session arbiter — when several
agents share one machine it serializes them, and it yields while you are actually using the
computer yourself.

This repository contains only the MCP server and the skill. It stays **neutral toward any stdio
MCP client** (dsh / Claude Code / Codex / Cursor / Cline / ZCode …) — nothing here requires dsh.

**Platform semantics (0.3.1 and later)**: the tools only work on Windows — they drive
user32/kernel32 and UI Automation. But **the package also imports elsewhere and the server
starts there**, answering `tools/list` as usual, so any client or directory crawler can
enumerate all 19 tools with their full schemas; actually calling a tool returns a clear
"requires Windows" error rather than the process failing to start at all. In 0.3.0 the import
itself raised, which made such crawlers unable to see the server at all
(`tests/linux-handshake.py` is the regression test for this property, and CI runs it on
`ubuntu-latest`).

## What it is

A stdio MCP server exposing 19 tools:

- **Observe** (read-only, callable at any time): `skyshot` (reads a window as a compact,
  diffable text tree — three orders of magnitude smaller than a screenshot), `element_at_point`,
  `read_element`, `find_elements`, `capture_window` (DPI-aware, cropped to the client area),
  `list_windows` / `find_window` / `get_window_rect`, `list_displays`, `cursor_position`,
  `clipboard_read`, `coexistence_status`
- **Element actions** (soft gate: serialized across agents, no physical input injected):
  `element_action` / `element_action_at` — press / set_value / select / toggle / expand /
  collapse / scroll_into_view / focus, delivered straight to the UIA element, so they
  **never steal focus and never care about z-order**
- **Physical input** (hard gate: serialized across agents + yields to the human): `click_at`
  (element path first, raw event as fallback), `send_keys`, `type_text` (targeted PostMessage),
  `clipboard_write`, `open_application`

Every action returns a **receipt** rather than a self-reported success: `action_sent` /
`effect_verified` / `foreground_changed` / `user-active` / `arbiter-busy`. "The call was
accepted" and "the effect happened" are two different things, and the tool separates them for
the agent.

## How it differs

There are already several mature open-source Windows implementations. dsh-cua's differences are
concentrated on one thing: **sharing a machine with a human.**

| | dsh-cua | [cua-driver](https://github.com/trycua/cua) | [ahk-mcp](https://github.com/anomalous3/ahk-mcp) | [lean-computer-use-mcp](https://github.com/Kvxw1105/lean-computer-use-mcp) |
|---|---|---|---|---|
| Element actions via UIA patterns (no focus steal, z-order irrelevant) | ✅ | ✅ (ax mode) | ❌ coordinate clicks only | via cua-driver |
| **Recent human input → refuse** | ✅ `user-active` | ❌ | ❌ | ❌ |
| Cross-agent serialization (multi-process) | ✅ named mutex | ❌ | ❌ | ❌ |
| Per-action effect assertion | ✅ three-state `effect_verified` | reports a delivery tier | ❌ | ❌ `state_changed` heuristic only |
| Foreground-steal side effect measured | ✅ `foreground_changed` | ❌ | ❌ | ❌ |
| Tool count | 19 | 59 | 15 | 6 |

**The key distinction is two things that are routinely conflated:**

- **"No focus steal" is a mechanism guarantee** — either a UIA pattern or a targeted
  `PostMessage`, so the cursor and keyboard focus are physically never touched. cua-driver has
  it (ax mode); **ahk-mcp actually does not** — it only has coordinate clicks, so every action
  moves the real cursor.
- **"Yield the moment you move" is a timing guarantee** — it reads the age of the human's last
  input via `GetLastInputInfo`, waits when it sees you using the machine, and on timeout
  **refuses** (`user-active`) instead of barging in. **None of the other three implementations
  in that table has this.**

`effect_verified` is likewise something the alternatives lack: it splits "the call was accepted"
from "the effect happened" and gives three states (`true` changed as expected / `false` accepted
but unchanged, downgraded to a failure / `null` no comparable state, i.e. unconfirmed). The
usual alternative is to re-observe once after the action and leave the judgement to the model.

**What dsh-cua does not do** (stated up front to avoid misunderstanding): no pixel/vision
grounding — interfaces a tree cannot express (canvas, games, remote desktop) are out of reach;
no record-and-replay; no isolation sandbox. There are better-suited tools for those.

## Install

You need **Windows x64 + an interactive desktop session + Python ≥3.10** to actually drive a
desktop. (The package installs and starts on Linux/macOS too, `tools/list` answers normally,
and a tool call then reports "requires Windows" — see "platform semantics" above.)

```bash
# Option 1: uvx, zero install (recommended)
uvx dsh-cua                      # runs the stdio MCP server directly

# Option 2: pip
pip install dsh-cua

# Option 3: from source
pip install git+https://github.com/Hutusion/dsh-cua.git
```

However you install it, **start the server with `python -m dsh_cua`**:

```bash
python -m dsh_cua                # depends on no executable being on PATH
```

> **Why the README does not say `dsh-cua-server`**: pip installs console scripts into the
> interpreter's `Scripts` directory, and **that directory is not necessarily on PATH** —
> measured on a stock python.org 3.12 install, neither the User nor the Machine PATH contained
> it, so `pip install dsh-cua` succeeded while `dsh-cua-server` reported command not found.
> `python -m` needs no PATH entry at all. The console script is still shipped and works when
> PATH does contain it.
>
> Options 1 and 2 both work today: the package is published on PyPI
> (<https://pypi.org/project/dsh-cua/>). If `uvx`/`pip` ever 404s, use option 3 — it always
> works.

## Wiring it up

Any MCP client; name the server **`win32`** (the skill's tool-name convention is
`mcp__win32__*`).

**`python -m` (no PATH dependency, recommended)**:

```json
{ "mcpServers": { "win32": { "command": "python", "args": ["-m", "dsh_cua"] } } }
```

**`uvx`**:

```json
{ "mcpServers": { "win32": { "command": "uvx", "args": ["dsh-cua"] } } }
```

More shapes are in [`examples/`](examples/): Claude Code / generic clients / a dsh
`cordis.patch.yml` fragment / the route modality declaration you need if you want the model to
read screenshots (`tr-route-settings.yml`).

## Skill (optional but strongly recommended)

[`skill/computer-use/SKILL.md`](skill/computer-use/SKILL.md) is the companion doctrine for using
these tools: the observe → locate → act → verify loop, receipt semantics, retry safety, and the
discipline of coexisting with a human. The model can use the tools without it, but with it the
model **picks the right path by itself** — the measured difference is large. Copy it into your
skills directory:

```bash
# Claude Code / generic agents
cp -r skill/computer-use ~/.agents/skills/
# dsh
cp -r skill/computer-use ~/.dsh/skills/
```

## Security model

| Tier | Operations | Gate |
|---|---|---|
| Read-only | the 12 observe tools | no gate, callable at any time |
| Soft | element actions, PostMessage typing, clipboard write, launching applications | cross-agent mutex (named mutex, multi-process, automatic serialization) |
| Hard | raw clicks, global hotkeys | mutex + `GetLastInputInfo` yielding: if the user typed recently it waits, and on timeout refuses with `user-active` instead of stealing the cursor |

Honest boundaries: yielding is a cooperation protocol, not a hard guarantee (the tight check
150 ms before injection narrows the window as much as possible); a few applications
self-activate even on `set_value` (the receipt reports `foreground_changed` truthfully); and
two operators on the same window has no technical solution — do not drive the same window the
agent is driving.

## Tests

```bash
python tests/verify-coexistence.py    # 25 checks: zero-input proof / cross-process mutex / synthetic human contention / kill switch
python tests/verify-p0-fixes.py       # the three P0s fixed in 0.2.0: each fails before the fix
```

The tests need no human cooperation — "user input" is synthesized with one real 1-pixel cursor
move, and the cursor is restored afterwards.

**The tests need a real interactive desktop session** (some checks create windows and address
them through UIA), so they **cannot run on a GitHub-hosted runner**. What CI does cover is the
part that needs no desktop: packaging and installation, module import, regressions for the diff
index and tree-line escaping, and the arbiter's decision logic — see
[`.github/workflows/ci.yml`](.github/workflows/ci.yml).

```bash
python tests/ci-desktop-free.py       # the local equivalent of the above, no desktop needed
```

## License

MIT
