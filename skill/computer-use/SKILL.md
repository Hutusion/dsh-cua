---
name: computer-use
description: "Drive a GUI — a desktop application or a web page — instead of editing files: read what is on screen, click, type, fill fields, operate a browser. Accessibility-first semantic actions (mcp__win32__* for the desktop, mcp__playwright__* for page content); screenshots and coordinate clicks are fallback only. Use when the task is 'operate this app/page' rather than 'change this code'. Not for tasks a shell or file tool can do."
---

# Computer Use

Accessibility first. Screenshots last. Every action is verified, never replayed blind.

## The loop

**Observe once, act once, verify.** Then observe again only if the next step needs fresh state.

1. **Observe** — `mcp__win32__tool_skyshot` returns the window's UI as a **text tree**. Do not take a
   screenshot to find out what is on screen.
2. **Act on an element** — `mcp__win32__tool_element_action_at` (by the line index the tree printed)
   or `mcp__win32__tool_element_action` (by a `ref` from `tool_find_elements` /
   `tool_element_at_point`). This is the primary path.
3. **Read the receipt** — `action_sent`, `effect_verified`, `foreground_changed`. See *Receipts* below.
4. **Re-observe only when the next step depends on current UI state.** Actions return a receipt, not a
   fresh tree, on purpose.

## Which tool family

| Target | Use |
|---|---|
| A desktop application's own UI | `mcp__win32__*` |
| Content **inside** a web page | `mcp__playwright__*` (DOM-level, so it is semantically precise) |
| A browser's own chrome (tabs, address bar) | either; `mcp__win32__*` sees it as ordinary UI |

Both can reach a browser window. Prefer playwright for page content and win32 for everything else —
do not run both workflows for the same action.

## Observing

### `mcp__win32__tool_skyshot` — the default read

Renders the control tree as text, one element per line:

```
0 window dsh cua test target
1 	edit Value: PLACEHOLDER, id: 100 (settable)
3 	button Press me
4 	text idle
```

Read it like that: `{index} {indent}{role} {name}{Value: ...}{(state)}`. Indentation is depth.
`(settable)` means the value can be written; `(focused)` means it holds keyboard focus.

**Every shot after the first is a DIFF** against the previous one — unchanged lines are omitted,
`~` marks a changed line, `+` an added one, and disappeared indices are summarised as
`Removed element IDs: 3-7, 12`. A window that has not changed costs about 100 characters, so
re-observing is cheap. That is the point of the tool: **a full tree is ~700 characters, a screenshot
is ~2.4 MB.**

**To act by index you need `disable_diff=true`.** A diff carries no indices for unchanged elements,
so you cannot address them from it. Take a full render when you intend to act.

`shot_key` is the window the diff baseline belongs to — the baseline lives in the server and outlives
your session, so check it when the answer matters. `next_index` is the index the next new element
would receive; indices are never reused.

### `mcp__win32__tool_find_elements` — when you know what you want

`role` (lowercase, as printed by skyshot: `button`, `edit`, `document`, `list_item`, …) and/or
`name_contains`. Returns elements with `ref`s you can act on directly. **At least one filter is
required** — an unfiltered search would be a tree dump, which is what skyshot is for.

**Select by name or role, never by position.** "The first `edit` in the tree" is usually not the field
you meant — in a browser it is the read-only address bar, not the page's input.

### `mcp__win32__tool_element_at_point` / `tool_read_element`

- `tool_element_at_point(x, y)` identifies the element at a PHYSICAL screen point and returns a `ref`.
  Read-only, moves nothing. Coordinates are the same space as `tool_capture_window`'s `bounds`. Use it
  to turn "the thing I can see at that spot" into something addressable — but prefer
  `tool_find_elements` when you can name the target, because a point is the weakest kind of identity.
- `tool_read_element(ref)` re-reads an element before you act on it. A `ref` goes stale when the UI is
  rebuilt; staleness is reported, never guessed.

### `mcp__win32__tool_capture_window` — fallback only

Returns `bounds` (the screen rect the image maps to) and `dpi_verified`. Image pixels and
`tool_click_at` coordinates are the same physical space **only when `dpi_verified` is true**; if it is
false, do not pair them.

## Acting

**Prefer the element path.** A UI Automation pattern is delivered to the element itself, so the window
is never raised, z-order never matters, and the user's focus is not stolen.

- `mcp__win32__tool_element_action_at(hwnd, index, action, text)` — index from a full skyshot.
- `mcp__win32__tool_element_action(ref, action, text)` — ref from `find_elements` / `element_at_point`.
- Actions: `press`, `set_value`, `select`, `toggle`, `expand`, `collapse`, `scroll_into_view`, `focus`.
  Only actions the element actually advertises are accepted; the refusal lists what it does support.
- **Typing: prefer `set_value` on the element.** It is semantic and needs no focus.

**Coordinate clicks are the fallback** — `mcp__win32__tool_click_at`. It tries the element path first
and reports which it used in `method` (`ax_press` = focus-free, `raw_event` = cursor injection). The
raw path requires the window foreground and reports `focus_stolen: true`. Use it only when
accessibility cannot express the target.

**Never translate image pixels into an element index.** A coordinate from a screenshot is only ever
usable as a coordinate.

## Receipts and retry safety

`action_sent: true` means the action **may already have happened**. Never replay it blindly.

- `effect_verified: true` — the pattern's state changed as expected.
- `effect_verified: false` — the call was accepted but nothing changed; reported as `ok: false`.
  Do not retry the same call; re-observe and pick a fresh target.
- `effect_verified: null` — this action exposes no comparable state (`press` is the common case), so
  the effect is **unconfirmed**. Confirm it another way if it matters — read the window again, or use
  an external oracle such as the file or the server the action was supposed to affect.
- `foreground_changed: true` — the action activated the window. On some apps this is unavoidable
  (setting a value on Notepad's document does it). Report it rather than hide it.
- `value_read_only` — the element's value cannot be written. Pick a different element; retrying will
  not help.
- `user-active` — the admission gate refused physical input because the user is actively using the
  machine. **Nothing was injected.** Do not retry immediately; use the element path or ask the user.
- `arbiter-busy` — another agent session holds the mutating lock. Nothing was sent. Wait, retry once,
  then report instead of looping.
- A stale `ref` or an out-of-range index is **refused**, not guessed. Take a fresh skyshot.

**A changed tree is not by itself proof that your action caused the change.** When it matters, verify
against something outside the UI.

## Focus discipline

The user is often working on the same machine.

- **Never activate or raise a window merely to make a semantic action work.** If an element action
  needs the window frontmost, that is a signal to look for a different element or action.
- `mcp__win32__tool_send_keys` and the raw click path **do** take focus; they refuse when the addressed
  window is not the foreground window rather than typing into the wrong place.
- Prefer the focus-free path even when it costs an extra observation.

## Sharing the machine with the user

The user is usually typing and moving the mouse while you work. Operations come in three grades:

- **Read-only** — `tool_skyshot`, `tool_find_elements`, `tool_element_at_point`, `tool_read_element`,
  `tool_capture_window`, `tool_list_windows`, `tool_get_window_rect`, `tool_clipboard_read`,
  `tool_list_displays`, `tool_cursor_position`. No input, no focus change: safe any time, in
  parallel with whatever the user is doing.
- **Element actions** (soft gate) — serialized across agent sessions, but they inject no physical
  input, so they can run while the user types. Do not operate the very window the user is working
  in; that logical contention has no technical guard.
- **Physical input** (hard gate) — `tool_click_at`'s raw_event path and `tool_send_keys`' global
  hotkeys share ONE cursor and ONE keyboard with the user. The gate waits for the machine to go
  input-quiet, then refuses with `user-active` rather than fight the user. When refused: **do not
  hammer retries** — switch to the element path, or tell the user what you need.

`tool_coexistence_status` reports the numbers behind a refusal (quiet window, last input age).
`tool_clipboard_write` and `tool_open_application` are mutating too (soft) — a clipboard write
destroys what the user last copied, so announce it when you do it.

## Browser specifics (`mcp__playwright__*`)

- Use the a11y snapshot (`mcp__playwright__browser_snapshot`) to see the page — it is text and cheap.
  `browser_navigate` / `browser_click` return the snapshot as a **file link**, not inline, so call
  `browser_snapshot` explicitly when you need to read the result.
- Act on elements by role and name (`browser_click` with a snapshot `ref`), not by pixel.
- For a Chromium window, `mcp__win32__tool_element_at_point` at a point inside the page returns the
  enclosing **`document`**, never the input inside it. To name a page field from the win32 side, use
  `mcp__win32__tool_find_elements` or `tool_skyshot` — that is exactly the gap the tree fills.
- The dsh-side browser runs with `--isolated` (profile in memory), so **it has no login state** and
  one is not carried between runs. If a task needs a signed-in site, say so rather than retrying.

## Anti-patterns

- Screenshotting to find out what is on screen. Use `tool_skyshot` — it is ~4 orders of magnitude
  smaller and names the elements.
- Acting from a diff. It has no indices for unchanged elements.
- Running an accessibility action and a coordinate click for the same target.
- Picking an element by position instead of by name/role.
- Replaying a call whose `action_sent` was true.
- Assuming `effect_verified: null` means success. It means unconfirmed.
- Raising a window to make a semantic action work.
- Retrying a `user-active` refusal in a tight loop. The user is busy; that will not get better by
  itself.
- Overwriting the clipboard without announcing it.
