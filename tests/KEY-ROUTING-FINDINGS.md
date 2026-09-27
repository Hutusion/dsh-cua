# Keyboard routing: what was measured, on this machine

**About the paths.** This document cites `_cmp\...` for the raw audit reports and the JSON those runs
left behind. `_cmp` sits *beside* this repository rather than inside it, so those citations are
traceability records from the machine the measurements were taken on — they say exactly which
artifact a number came from, but they are not links you can follow from a checkout.

**Everything else here IS runnable from a checkout of this repository:**
`tests/verify-key-routing.py` (the instrument) with `tests/accelerator_target.py` (its fixture), plus
one probe per finding that needed one — `tests/verify-minimized-tree.py`,
`tests/verify-smto-empty-and-hang.py`, `tests/verify-explorer-minimize.py`,
`tests/verify-note-scope.py`, `tests/verify-b3-zcode.py`, `tests/verify-type-text-fix.py`. Those are
what every claim below can be re-run from; the `_cmp` citations are history.

Instrument: `tests/verify-key-routing.py` (+ `tests/accelerator_target.py`).
Measured 2026-09-26 on Windows 11 26200.9457.
**Every conclusion below was independently audited by three adversarial subagents; see
[Audit trail](#audit-trail). Three of the first version's conclusions did not survive.**

Reproduce:

```
python tests/verify-key-routing.py inventory    # read-only, no input, no focus change
python tests/verify-key-routing.py fixture      # owned window: which posted routes land
python tests/verify-key-routing.py calculator   # real UWP/XAML host
python tests/verify-key-routing.py chromium     # real Chromium host, isolated profile
python tests/verify-key-routing.py typetext     # does bridge.type_text land, and admit it
```

## The question

`send_hotkey` is one of dsh-cua's only two hard-gated paths, and it is worse than gated.
`bridge.send_hotkey` calls `_ensure_visible` FIRST (`bridge.py:573`) — `ShowWindow(SW_RESTORE)`
+ `SetForegroundWindow` — and only THEN checks whether the target reached the foreground
(`bridge.py:574`). So a hotkey has exactly two outcomes: it steals the user's foreground, or it
is refused with `target-not-foreground`. **There is no third outcome.**

That steal is invisible to the arbiter by construction. `arbiter.py` says so itself:
"Programmatic activation (SetForegroundWindow inside `_ensure_visible`) is not itself an input
event." `GetLastInputInfo` cannot see it, so the hard gate does not cover it.

So: can a hotkey be delivered by posted window messages instead — no gate, no foreground change?
The measurement below answers that **per target class**.

## The answer, by target class

| target class | posted-key route, window in BACKGROUND | focus change | how it was established |
|---|---|---|---|
| Classic Win32, handler trusts the message | **works, plain `PostMessage`** | none | owned fixture, `keydown_message` = 1 |
| Classic Win32, handler/loupe consults `GetKeyState` or uses `TranslateAccelerator` | **works with `AttachThreadInput` + `SetKeyboardState`** | none | owned fixture, accel 0→1 and `kd_gks` 0→1, 7 controls + 3 extra probes |
| **UWP / XAML** | **works — when addressed to the app's own `CoreWindow`** | none | real Calculator: display `显示为 0`→`显示为 5`, two runs |
| **Chromium page content** | **fails in the background; works once the window is foreground** — all three posted routes land there | (already foreground) | isolated Edge, both passes now run by the harness itself: foreground `V=5` -> `V=55` -> `V=555`, background no change |
| `WM_CHAR` text, any app with a child edit | must target **the control**, never the frame | – | fixture + **4 real apps** (charmap, mstsc, 7-Zip, Everything), 5/5 |
| consumers of `GetAsyncKeyState` / hooks / RawInput | **not covered by any of the above** | – | measured: staged state leaves `GetAsyncKeyState` FALSE |

The practical reading: the de-gate route is **broader than the first version claimed**, but it is
**not universal**. Chromium content in the background is the one class that genuinely needs either
the gate or a non-keyboard route (CDP/DOM).

## What the first version got wrong

Recorded in full because two of these were stated to the user as headline conclusions.

1. **"UWP/XAML cannot be de-gated — a real UWP host publishes 0 AcceleratorKey and posted keys do
   not move its display."** — **REFUTED.** The probe posted to the `ApplicationFrameWindow`, which
   is owned by `ApplicationFrameHost.exe`; the app's own window is the child
   `Windows.UI.Core.CoreWindow`, owned by `CalculatorApp.exe`. A posted `VK_5` addressed to the
   **CoreWindow** moves the display **in the background, with the foreground unchanged**. Arrival
   was calibrated on both handles. This was an **addressing** artifact, and it inverted the
   conclusion for a whole target class.

2. **"Real Chromium page content is unreachable by any posted-message route."** — **REFUTED as
   stated.** It is unreachable **while the window is not the active window**. Phase D always handed
   the foreground back before probing, so it never tested the foreground case — and the foreground
   case works: the same posted `WM_KEYDOWN` inserts the character into the page. This was an
   **activation** artifact that the harness's own procedure guaranteed while its conclusion blamed
   the message route.

3. **"`AcceleratorKey` is close to absent — 8 values total, zero from page content."** —
   **PARTLY REFUTED.** The 8 reproduces exactly, but it is a **sample-derived lower bound**, not a
   census. A wide net finds **35** (raw view) / **18** (control view). Offscreen pruning alone costs
   6 of 14 keys on one real page, **all of them page content**: Chromium maps `aria-keyshortcuts`
   → UIA `AcceleratorKey` verbatim, so the channel is real. Panel of the loss:

   | variant | nodes | AcceleratorKey |
   |---|---|---|
   | harness rule | 137 | 8 |
   | harness rule, caps raised to 20000/depth 80 | 137 | 8 |
   | control view, offscreen **included** | 1 474 | 14 |
   | raw view, offscreen pruned | 422 | 8 |
   | raw view, offscreen **included** | 3 072 | 14 |

   Caps cost nothing; **pruning and the view choice cost everything.** On a second Edge window the
   control view returned 68 nodes and **no `document` at all** where the raw view returned 756 nodes
   and 6 documents, hiding 16 of 18 keys. That is a property of `uia._walk`, so it affects shipped
   `skyshot`/`find_elements`, not just this harness — see [Defects in dsh-cua](#defects-found-in-dsh-cua).

4. **"The call that works is also the only call that doesn't steal focus."** — **REFUTED.** In
   Phase E the FRAME call ran first and had already raised the fixture, so the EDIT call that
   followed had nothing left to steal. With the foreground reset before each call, **both** steal
   it (`1183062 → 135864`). I reported a property of my probe ordering as a property of the code.

5. **"Zero from any Electron app"** — **UNPROVEN, not confirmed.** Three of five Electron apps
   (ZCode, QQ, Clash Party) returned a 18–24-node tree with no `document` node at all. A tree that
   never materialises cannot support the word "zero". Only Token Monitor (602/1062 nodes) is a
   genuine measured zero.

6. **"The foreground was unchanged in every probe of every phase."** — **not robust.** It did not
   reproduce in audit: a `VK_ESCAPE` probe reported `fg_changed=True` while the user was active,
   because the harness attributes any foreground change to the probe. The claim holds for the
   fixture (where it was verified with ~296 000 continuous samples, single distinct value), not as
   a blanket statement about phases C and D.

## Findings that survived audit

### `AttachThreadInput` + `SetKeyboardState` de-gates classic Win32 (audit A: CONFIRMED)

| probe | accel | trusted message | used `GetKeyState` | focus changed |
|---|---|---|---|---|
| `WM_KEYDOWN 'F'`, no modifier | 0 | **1** | 0 | no |
| `Ctrl+F` posted (in accel table) | **0** | **1** | 0 | no |
| `Ctrl+G` posted (NOT in accel table) | – | **1** | **0** | no |
| **`Ctrl+F` + AttachThreadInput** | **1** | 0 | 0 | **no** |
| **`Ctrl+G` + AttachThreadInput** | – | **1** | **1** | **no** |

Audit A's controls exclude every alternative cause, each as a fresh run with the state file deleted:

* attach **without** `SetKeyboardState` → 0
* `SetKeyboardState` **without** attach → 0, *while the harness's own `GetKeyState` reads Ctrl down*
* state staged then **restored before posting** → 0
* state live at post but **detached before processing** → 0 (made deterministic with `SuspendThread`)
* **no route without the attach exists**: lParam context bit 29, extended bit 24, `WM_SYSKEYDOWN`
  Ctrl+F, `WM_SYSKEYDOWN` Alt+F, `PostThreadMessage`+`SetKeyboardState` all 0.

The cleanest single piece of evidence is arrival, not counters. Without the attach the raw log
contains the F keydown arriving and being declined:

```
c2 (SetKeyboardState only, NO attach)  menu_accel 0, kd_msg 1
  {seq:2, msg:0x100, wparam:0x46, acted:"keydown_message"}
```

With the attach, **that entry is absent entirely** — `TranslateAccelerator` consumed it:

```
c0c (attach + SetKeyboardState)        menu_accel 1, kd_msg 0
  {seq:1, msg:0x100, wparam:0x11}
  {seq:2, msg:0x111, wparam:0x107d1, acted:"menu_accel(code=1)"}
```

**The difference is modifier state at processing time, not message delivery.** Corroborated
independently by cua's own source (`cua-driver-e2e/tests/harness_wpf_test.rs:1470`), which notes
that the *system input queue* path "DOES update GetKeyState" — implying the posted path does not.

Also established by audit A: **only the modifier STATE matters** — attach + `SetKeyboardState` +
posting `VK_F` alone, with no Ctrl keydown message, still fires the accelerator.

Caveats that bound this:
* **`GetAsyncKeyState` stays FALSE** while the staged state is live. Apps polling it, or using
  hooks / RawInput, are **not** reachable this way.
* `AttachThreadInput` merges input queues — hang propagation and UIPI limits are untested — and
  it grants `SetForegroundWindow` ability as a side effect.
* The `menu_accel` counter is **not accelerator-specific by construction**: a hand-posted
  `WM_COMMAND(0x107d1)` increments it too. The probe holds only because it is paired with the
  *missing* `WM_KEYDOWN` in the log.

### `bridge.type_text` fails silently (audit B: CONFIRMED, and generalized)

Audit B's leading counter-hypothesis — that the fixture is unrepresentative because real apps with
edit controls are dialogs whose dialog manager forwards a frame-level `WM_CHAR` — was **refuted by
measurement**. 5/5 targets, 2 dialogs and 3 plain-class windows:

| target | top-level class | `WM_CHAR`→top-level lands? | `WM_CHAR`→control lands? | MCP tool default lands? |
|---|---|---|---|---|
| `accelerator_target.py` fixture | plain | **no** | yes | no |
| charmap.exe | `#32770` dialog | **no** | yes (all 3 text children) | no |
| mstsc.exe | `#32770` dialog | **no** | yes | no |
| 7-Zip File Manager | plain | **no** | yes | no |
| Everything | plain | **no** | yes | no |

The characters **do arrive** at the frame's window procedure and the frame does not forward them,
so this is not a rejected post — `PostMessage` is simply not a routed-input API:

```
type_text(FRAME, 'AAA')   frame RECEIVED 3 WM_CHAR(s);  EDIT text='EDIT-CONTENT'
type_text(EDIT,  'BBB')   frame RECEIVED 0 WM_CHAR(s);  EDIT text='BBBEDIT-CONTENT'
```

Landed and not-landed receipts are **identical in shape**: `{"ok": true, "reason": "typed",
"typed": 3, "text_length": 3}` — the only difference is the `hwnd` echoed back, which is the
argument, not an observation. `effect_verified` exists **only** in `uia.py`, never in `bridge.py`,
against the server's own instruction text (`server.py:97-101`) and `README.md:68-71`.

The focus steal is `SetForegroundWindow` at `bridge.py:278` (measured one primitive at a time:
`ShowWindow(SW_RESTORE)` is a no-op on a non-minimised window and `BringWindowToTop` alone does not
move the foreground). Its result dict is discarded at `bridge.py:751`. Unlike the text failure,
**the steal is already disclosed** in the docstring (`bridge.py:740-743`) and the tool description
(`server.py:307`).

## Defects found in dsh-cua

| # | defect | evidence | status |
|---|---|---|---|
| 1 | `bridge.type_text` returns `ok: true` for text that never reaches the control; no `effect_verified` on the whole path | 5/5 targets, audit B | **FIXED** — the control is resolved and its text read back |
| 2 | `bridge.type_text` discarded `_ensure_visible`'s rich result, so the raise was invisible to the caller | audit B | **FIXED** — `raise` and `foreground_changed` are in the receipt |
| 3 | `_ensure_visible(child)` always reported `{"raised": false, "method": "failed"}` — it compared a root reduction against a child hwnd, so it **under-reported the raise it had just performed** | audit B | **FIXED** — comparisons go through `_root()` |
| 4 | `uia._walk`'s use of `ControlViewWalker` was said to hide whole page subtrees (audit C: 68 nodes and no `document` where the raw view had 756 and 6) | audit C | **REPRODUCES — but the cause is not the walker.** The window was MINIMIZED and its page tree had never been built. Change still not applied, and the docstring's stated reason was wrong and is fixed. See below |
| 5 | `_walk` prunes offscreen subtrees, discarding accelerator keys that live in the page's document | audit C, reproduced | real; `include_offscreen` already exists and is the lever |
| 6 | `tool_type_text` defaults to `foreground_window()` — top-level — and nothing told a caller to pass the control instead | audit B | **SUPERSEDED** by fix 1: the tool now resolves the control itself |
| 7 | `skyshot`/`find_elements` answered a minimized Chromium window with its browser chrome and no page, and **nothing in the receipt distinguished that from an empty window** | found while settling defect 4 | **FIXED** — both report `minimized`, with a `note` when true |
| 8 | `uia._walk`'s docstring justified `ControlViewWalker` with a factually wrong description of the extra raw-view nodes | re-measured here | **FIXED** — replaced with the measured description and the two numbers that actually decide it |

Round 2 — found by an independent adversarial review of the fixes above, then re-verified here:

| # | defect | evidence | status |
|---|---|---|---|
| 9 | `_read_control_text` used the **synchronous** `SendMessageW`. A target that is not pumping messages (hung, modal loop, suspended) blocks it forever, and the two calls sit inside `type_text`'s `try` — so the block also means `finally: gate["release"]()` never runs and the cross-process mutex stays held. **New surface: 0.3.4 only ever `PostMessage`d here.** | measured: with the fixture's UI thread suspended, the shipped call was **still blocked at 8 s** in a child process, while `SendMessageTimeoutW` returned in **1200 ms** | **FIXED** — `SendMessageTimeoutW` + `SMTO_ABORTIFHUNG`, and a timeout is reported as `effect_verified: null` (unconfirmed), not as `no-effect` |
| 10 | The same call returned `""` for a **destroyed** control and for a genuinely **empty** one, so "I could not read it" reached the caller as "your text did not land" — `reason: no-effect`, with an error message that blamed a read-only control. The docstring claimed `None` meant "refused outright", which the implementation never produced. | measured: `_read_control_text(0x1234)` returned `''`, not `None` | **FIXED** — the return value of `SendMessageTimeoutW` is a success flag (measured on a real empty cross-process `RICHEDIT50W`: ret=1, `lpdwResult`=0), so a control that does not answer now returns `None` |
| 11 | `check-ctypes-names.py` cannot see any `user32.X.argtypes = ...` line — it polices the ctypes *namespaces*, and says so. Deleting one required prototype was invisible to the entire suite, and a missing prototype truncates pointer arguments on 64-bit. | deleted `user32.GetGUIThreadInfo.argtypes` and ran the suite: the old checker exited **0** | **FIXED** — `tests/check-ctypes-prototypes.py`, wired into `ci.yml`; verified by deleting the same line and watching it exit 1 |
| 12 | `_is_read_only_control` applied `ES_READONLY` (0x0800) to every class in `_TEXT_CLASS_PREFIXES`. That bit is defined **per class**: Scintilla implements read-only through `SCI_SETREADONLY`, and a third-party `TextBox`/`TextEdit` may give 0x0800 an unrelated meaning. The failure mode is not a mislabel — it silently picks the WRONG TARGET. | code reading; `ES_READONLY` is documented for Edit controls | **FIXED** — only `edit`/`richedit` are judged by it, and every other class returns `None` ("unknown") rather than `False` ("writable") |
| 13 | `effect_verified = after != before` proves the control's text CHANGED, not that **these** characters are the change: the window has just been raised and a human may be typing in it. The `no-effect` error message led with "the control is read-only" — a guess presented as a diagnosis. | code reading; the raise is documented and measured | **FIXED (semantics only)** — the message is now neutral, and a length that does not match the text sent is reported in `effect_note` instead of being presented as success. The field is NOT renamed and the predicate is NOT tightened: a stricter predicate would introduce false negatives |
| 14 | The `minimized` note explained a small tree with Chromium's build-only-when-visible mechanism and stated it as the cause for **every** minimized window | round-2 review: Notepad keeps a complete tree (35 = 35) and still got the note; reproduced here independently | **FIXED (the claim, not the trigger)** — the note is now mechanism-neutral and names what was measured per family. It still fires for every minimized window: see the app-family matrix below for why that is the accepted trade |

### The app-family matrix behind defect 14

What a minimized window hides is **not uniform**, and the first version of the note assumed it was:

| application | minimized | restored | note appropriate? |
|---|---|---|---|
| Chromium / Edge, minimized **before anything read it** | browser chrome, **0 documents** | 50 nodes, 1 document | yes — the page really is absent |
| Chromium / Edge, minimized *after* it was built | keeps its document (26 / 26 / 25 nodes across three states) | — | note is harmless |
| **Explorer** — reproduced here on an Explorer window this test opened itself | **8 nodes** (control **and** raw) | **44 nodes** | yes — the tree really did degrade |
| Notepad (reproduced here) | **35 nodes** | **35 nodes** | **no — the tree is complete** |

The Explorer row started as the round-2 reviewer's measurement (they got 40 -> 8) and is now
reproduced here, on a window this test opened in its own temp directory — never the user's, and the
probe **refuses to run at all** if `explorer.exe <dir>` reuses an existing window instead of
opening one. It adds two things the reviewer's run did not:

* **A never-queried minimized window is 8 nodes as well** (not only a previously-built one), so
  Explorer's collapse is not about *when* the tree was built — for Explorer the variable really is
  `IsIconic`.
* **The raw view collapses with it, to 8**, so this is not a view-filter effect: the window stops
  publishing rather than the control view hiding things. That rules out "switch to `RawViewWalker`"
  as a fix for this application entirely.

So the note over-warns for Notepad. That is a deliberate trade, not a leftover: the two cases
cannot be told apart from the tree, because a chrome-only Edge window has 34 nodes and a complete
Notepad window has 35. Detecting "the tree looks degraded" would need a per-application baseline
that does not exist, and comparing the two UIA views costs a second walk on every read. The note
was therefore rewritten to state what is true in both cases — a small tree is not proof that the
window is empty, and `minimized` says why that might be — rather than to claim a cause it cannot
know. `minimized` itself is a fact and is unaffected.

### The fixes to `bridge.py` / `server.py`

`type_text` now **resolves the text control** rather than trusting the handle it was given, and
**reads the control's text back**:

```
resolved_by='first-writable-descendant'  target_hwnd=<the EDIT>  effect_verified=True
    text 'EDIT-CONTENT' -> 'AAAEDIT-CONTENT'          (was: ok=True, nothing typed)
resolved_by='input-is-a-text-control'    effect_verified=True   (explicit control still works)
ok=False  reason='no-effect'  effect_verified=False   (read-only control — was: silent ok=True)
_ensure_visible(<child>)  -> {'raised': True, 'method': 'SetForegroundWindow',
                              'addressed': <child>}   (was: {'raised': False, 'method': 'failed'})
```

Resolution order: the control itself if it is already a text control → the thread's focused
control (`GetGUIThreadInfo`) → the first **writable** descendant text control. Read-only controls
(`ES_READONLY`) are never preferred, because a window holding both an input box and a read-only
text area would otherwise resolve to the one that can never receive text. When no such control
exists — a browser or Electron window, the common case — the window itself is addressed as before
and the receipt says the effect is **UNCONFIRMED** rather than claiming success.

`tool_type_text` now carries the whole receipt through. It previously returned only
`{"success", "hwnd", "text_length", "time_ms"}`, discarding `effect_verified` — which is why the
MCP-level receipt for a landed and a not-landed call were byte-identical.

Verified 6/6 (`tests/verify-type-text-fix.py`), and the project's own checks still pass:
`ci-desktop-free.py`, `compileall src`, `check-workflow-shell.py`, `check-ctypes-names.py`,
`check-handshake-classifier.py`.

### The proposed `_walk` change was NOT applied — and the reason changed twice

Audit C reported that on a 16-tab Edge window `ControlViewWalker` returned 68 nodes and **no
`document` at all**, where `RawViewWalker` returned 756 nodes and 6 documents. That would be a
defect in shipping `skyshot`/`find_elements`, so it was re-measured here before touching shipped
code.

**First answer — "it does not reproduce" — was wrong.** Audit C's artifact names the window
(`views-C.json`: hwnd 133162, `deepseek吧-百度贴吧 和另外 15 个页面`). It was still alive, and
re-measuring it gives their numbers exactly: control 68 nodes / **0 documents**, raw 756 / 6. The
non-reproduction had been taken on a **different window**, and the row this document published for
the many-tab window (26 242 control nodes, 1 document, not truncated) does **not** reproduce on any
window on this desktop now. That row is withdrawn.

**Second answer, and the one that holds: the walker is not the cause — the tree was never built.**
The obvious confound is that the window is minimized (4 of the 7 Chromium windows here were). It is
a confound, and it was tested on a window this test owns, minimized **before anything queried it**:

| window state | control nodes | named | documents |
|---|---|---|---|
| minimized before any query | 33 | 14 | **0** ← browser chrome only, no page |
| restored | 50 | 22 | **1** |
| minimized again, after it was built | 37 | 17 | **1** |

Two further readings agree: an `--app` Edge window minimized and restored gives an identical tree in
all three states (26 / 26 / 25 nodes — a one-node wobble, not an effect), and a window minimized
*after* its tree was built keeps its page document.

> Chromium builds the active tab's accessibility tree only for a **visible** window. A window
> minimized before anyone read it exposes browser chrome and no page, and no read can build that
> tree while it is minimized — `include_offscreen=true` does not recover it (measured: 68 nodes,
> still 0 documents).

That is a fact about when Chromium builds a tree, not about which walker is used. It is the input
to fix 7, which puts `minimized` in the receipt so a chrome-only answer stops looking like an empty
window.

**Scope: Chromium, and only Chromium.** Round 2 established that this is *not* the general
behaviour of a minimized window, and the Explorer half was reproduced here (`tests\verify-explorer-minimize.py`):
Explorer goes **44 elements -> 8 on every minimize**, back to 44 when restored, and a
never-queried minimized Explorer window is 8 as well — so for Explorer the state *is* `IsIconic`,
not the build history. Notepad does not degrade at all (35 -> 35 again) — see
[the app-family matrix](#the-app-family-matrix-behind-defect-14). So
"Chromium builds the tree only for a visible window" stays as a statement about Chromium, and the
generalizable claim is the weaker one that actually matters for the receipt: **a minimized window
may not expose its full tree, and which applications do that is application-specific.** That
weaker claim is what the code now reports and what the note now says.

**Third: switching to `RawViewWalker` would be a net loss, for two measured reasons — and the first
one was carried by the WRONG WINDOW for most of this session.**

Both my reading and the reviewer's used **ZCode**: control 77 named vs raw 26, a 51-element gap, and
the strongest evidence anyone had. It was taken with ZCode **minimized**, and this document had
already established that a minimized window does not publish its full tree. Measured directly
(`tests\verify-b3-zcode.py`, which restores the window, measures, then puts the window state back):

| ZCode state | control nodes / named | raw nodes / named | named LOST if raw |
|---|---|---|---|
| restored (stable) | 109 / **87** | 232 / **110** | **0** |
| minimized | 109 / 87 | 232 / 110 | 0 |
| restored again | 109 / 87 | 232 / 110 | 0 |

On a restored ZCode the raw view is a strict **superset** — all 87 of control's names are among raw's
110. **So the `raw 77 / 26` reading was a minimization artifact and ZCode does not support this reason
at all**; every argument resting on that number was resting on a confound.

The reason survives anyway, on a window that is **not** minimized — found by adding the missing
direction to `--compare-views` (`LOST if raw`, the column that actually decides this):

| window | minimized | control named | raw named | hidden by control | **LOST if raw** |
|---|---|---|---|---|---|
| **Token Monitor** | **no** | **7** | **6** | 1 | **2** |
| msedge (tabbed) | yes | 68 | 151 | 84 | 1 |
| ZCode | yes | 77 | 26 | 3 | 54 ← the artifact |
| 13 others | — | — | — | 0 | **0** |

Which names go missing decides whether it matters. On Token Monitor the raw view would drop
**`'厂商色'`** and **`'高级 维护与配置'`** — real, user-facing labels — while the only name it *adds*
is `'Chrome Legacy Window'`, Chromium's internal window name. Trading two meaningful labels for one
piece of noise is not a good trade, so neither view is a superset **in either direction**.

* **The extra raw-view nodes are not what the docstring claimed.** They are real named elements —
  collapsed Edge menus and side panes (`下载`, `标签页栏`, `缩放: 100%`), wallet and translate
  surfaces that are not displayed, and the documents of **background tabs** — i.e. largely UI that is
  not on screen at all. `raw nm` is larger on 13 of 16 windows, by up to 96 names on a tabbed Edge
  window.

**One row here is still unmeasured.** The tabbed-Edge row loses `'新建标签页'` while **minimized**,
and has not been re-measured restored — that means restoring the user's browser window. It is the
only row that could still move this claim.

So `_walk` keeps `ControlViewWalker`, and its docstring now states this instead of the false claim
that the extras were "separators, groups and layout scaffolding" (fix 8). Audit C's reading of the
extras — "exactly the thing Claim A says is zero", i.e. displayed page content — is not what they
turn out to be.

`inventory --compare-views` walks every window with **both walkers and identical pruning** and
prints nodes / named / documents for each, so all three reasons are re-checkable rather than
asserted.

What **did** reproduce unchanged is the other half: **offscreen pruning**, which is the real lever
and was already a parameter.

| inventory pass | elements | AcceleratorKey |
|---|---|---|
| default (what shipped `skyshot` sees) | 326 | **8** |
| `--include-offscreen` (the census) | 5 130 | **14** |

The six extra keys are page content — `hyperlink 'Hutusion' AcceleratorKey='Alt+ArrowUp'` and
friends — because a page's scrolled-out rows are offscreen. `inventory` gained an
`--include-offscreen` flag so the census is reachable without changing what the tool ships.

Note on defect 1's severity: the recommended path works. `skill/computer-use/SKILL.md` never
mentions `tool_type_text`; its guidance is "prefer `set_value` on the element", and audit B measured
`uia.element_action(ref, 'set_value', ...)` on charmap's control landing with **`effect_verified:
true`**. So a well-behaved agent may never hit defect 1 — which bounds it without making the tool
correct.

## Harness defects found by audit, and fixed

* **`phase_calculator`'s cleanup killed the wrong process.** It resolved the pid of the
  `ApplicationFrameWindow` — `ApplicationFrameHost.exe`, which hosts **every** packaged UWP app —
  and killed it, while its own comment claimed "never every packaged app". It was not theoretical:
  `Nahimic3.exe` was in the inventory minutes before a run and gone after. Now resolves the app's
  own `CoreWindow` and **refuses to kill by an unverified name**. Verified: `CalculatorApp` killed,
  `ApplicationFrameHost` preserved.
* **`phase_chromium` leaked its temp profile** every run — `shutil.rmtree(..., ignore_errors=True)`
  ran while Edge still held the directory. Now waits for exit, retries, and reports failure.
  The leaked profile from earlier runs was removed.
* **`_ctrl_combo_with_attached_state` printed `GetKeyState_saw_ctrl` as if it were evidence.** It is
  a read-back of the *harness's own* queue and returns `true` even with no attach (audit A control
  2), so it carries zero target information. Renamed to
  `harness_side_readback_not_target_evidence`. (The findings doc never relied on it — its columns
  were always target-side counters.)
* `phase_calculator` now probes **both** the frame and the CoreWindow, since comparing them is the
  finding; probing only the frame is what produced the false negative.
* **The probes recorded whether the observable changed, but never whether the message was even
  accepted.** `PostMessageW`'s BOOL was discarded, so "delivered and ignored" and "never queued"
  were indistinguishable — the conflation at the root of both bad negative results. Every probe now
  reports its acceptance list alongside its counters.
* **`inventory` and `chromium` never called `uia.warm_up`**, which exists. Chromium/Electron build
  their accessibility tree lazily, so an un-warmed window reports an empty tree — which reads as
  "publishes nothing" rather than "nothing built yet". Three Electron apps were reported as zeros
  for this reason. Now warmed before walking.
* **A crashed run leaked a live fixture window.** `phase_fixture`'s cleanup called
  `PostMessageW(..., WM_CLOSE, ...)` with `WM_CLOSE` undefined; the `NameError` propagated out of
  the `finally` block, so `proc.terminate()` — the next statement — never ran. The stray
  `dsh cua accelerator target` window survived for hours and the `inventory` phase later sampled it
  as though it were a real application. Cleanup is now a single `_reap()` helper with each step
  individually guarded, so one failure cannot skip the rest.
* **`inventory` now prints that its number is a lower bound**, with the measured losses named, so
  the mistake this document made is not repeated by the next reader.
* **`inventory` sampled ONE window per `(exe, class)`.** Audit C measured that of three Edge
  windows, the two not sampled held 4 of the 18 control-view keys and 21 of the 35 raw-view keys —
  while the function's own docstring asserted that "walking every Edge window would be pointless",
  which the measurement contradicts. It now walks **every** visible titled window, and says so
  loudly when the `--windows` cap drops any, because a silently truncated census reads exactly like
  a census.
* **No probe watched the foreground — they only sampled it before and after.** Audit A built a
  polling watcher to test this harness's own "the foreground is unchanged" claim and got
  284 000-296 000 samples with a single distinct value: a far stronger statement than a two-point
  comparison, which cannot see a steal that comes forward and goes back between the reads. A
  `ForegroundWatch` thread now samples every probe in phases B, D and E, and it has already earned
  its place: `typetext` reports the foreground passing through `[10162834, None, 5377068]` during a
  `type_text` call that the two-point comparison reduced to a bare `True`.
* **`inventory` now walks both UIA views on request** (`--compare-views`), with identical pruning,
  because "what does the control view hide?" is not answerable from a control-view walk. That flag
  is what settled defect 4, and it replaced an argument with a table.
* **`phase_chromium` never probed the foreground case, then generalised from the background one**
  (audit C, instrument gap 3). It handed the foreground back before probing, so "Chromium needs
  activation, not a different message route" was audit C's result rather than this harness's. It
  now runs **both** passes, and the foreground one needs no raise at all because the window
  launches into the foreground — foreground `V=5` -> `V=55` -> `V=555`, background no change, for
  all three posted routes. The first attempt at this got the ordering wrong and printed the
  failure honestly: raising the window *again* after giving the foreground away does not work (the
  foreground lock), so the run said the foreground case was not measured instead of quietly
  reporting background results twice.

## Corrections made while measuring

Recorded because each produced a confident wrong answer first.

* **`WM_COMMAND` notification code.** The fixture accepted only code 0, so the accelerator probe
  reported "never fired" while the raw log already contained `WM_COMMAND 0x107D1` (id 2001,
  **code 1**). A false negative, caught only by reading the raw log rather than the summary.
* **Stale state file.** A leftover file was read as the new run's baseline, producing negative
  deltas (`char=-2`). The state file is now removed before launch.
* **UWP window selector.** Matching on `ApplicationFrameHost.exe` picks the wrong app — that process
  hosts every packaged UWP app. The first run reported a 1-node tree, which looked like a fact about
  UWP and was a fact about the selector.
* **"The UWP frame is the wrong UIA root."** A hypothesis formed from that 1-node reading, and it is
  false: the frame returns 75 nodes and the CoreWindow child 67. The reading was the selector bug.
* **Fixture child race.** `FindWindowW` returns before the fixture's children exist; Phase E took
  `'<no EDIT child>'` as its baseline and reported LANDED for a call that had not landed. Fixed with
  `_wait_for_edit`, and Phase E now refuses to measure rather than accept an error string as a baseline.
* **Order-dependent foreground measurement** (see correction 4 above) — the worst of these, because
  it was published as a headline.
* **Addressing and activation artifacts** in phases C and D (corrections 1 and 2) — the two
  negative results that inverted a target class each.
* **"The windows where the control view hid a document were all minimized" — a correlation I
  proposed and then killed myself.** 4 of the 7 Chromium windows here showed control < raw, and all
  4 were minimized, which looked like an explanation. It did not survive a controlled test: a
  window minimized *before* the first query and one minimized *after* it behave differently, so the
  state that matters is **when Chromium built the tree**, not `IsIconic` on its own. The `chromium`
  phase now tests that on a window the test owns instead of inferring it across windows that differ
  in every other way too.
* **A check of mine compared three readings by tuple identity** and so printed "the tree CHANGED
  with window state" for a 26-vs-25 node difference — a one-node wobble in a cross-process UIA walk
  reported as an effect. It now compares numbers and states the spread.
* **The watcher's report key was wrong, and a passing run hid it.** `report()` returns `distinct`
  and two print sites asked for `others`. Both sat inside a conditional expression, so a probe that
  never saw a change never evaluated the bad branch; it raised only in `typetext`, where the
  foreground really does move. A latent defect that a green run conceals is worth writing down.

## Not measured

* **Real classic Win32 applications** for the `AttachThreadInput` route. It is proven on a fixture
  that reproduces the mechanism (`TranslateAccelerator`, `GetKeyState`), not on LibreOffice or FAR.
* **`GetAsyncKeyState` / hook / RawInput consumers** — measured as *not* reachable, and no
  alternative route for them was found.
* **`AttachThreadInput` failure modes**: hang propagation, cross-integrity (UIPI) refusal,
  elevated targets.
* **Chromium via CDP** — the expected non-keyboard route for that class.
* **Minimised targets**, multi-monitor, targets whose message loop runs on a different thread.
* **Which class real hotkey traffic falls into.** The desktop survey suggests Electron/Chromium is
  common here (ZCode, QQ, Clash, Token Monitor, DSH Shell are all `Chrome_WidgetWin_1`), which would
  make the Chromium background limitation the practically important one — but that is an inference
  from process names, not a measurement of usage.

## Audit trail

Three adversarial subagents, disjoint scopes, each instructed to falsify and to write its own
probes rather than only re-running this harness.

| auditor | scope | verdict | report |
|---|---|---|---|
| A | the `AttachThreadInput` mechanism | **CONFIRMED** (7 controls + 3 extra probes); added the `GetAsyncKeyState` limitation | `_cmp\auditA\REPORT.md` |
| B | the `type_text` defect | parts 1 & 2 **CONFIRMED** (5/5 real apps); part 3 focus half **REFUTED**; found defect 3 | `_cmp\auditB\REPORT.md` |
| C | inventory claim + both negative results | claim A **PARTLY** (8 is a lower bound; page content is not zero); claim B **REFUTED**; claim C **REFUTED as stated** | `_cmp\auditC\REPORT.md` |

Auditors also self-reported their own errors — B's leading hypothesis was the one the task hoped
for and it died on measurement; C first concluded the DOM was not exposed to UIA and was corrected
by replicating the harness's exact call. Their near-misses are recorded in their reports.

### Round 2 — an adversarial review of the fixes themselves

The fixes above were handed to two further reviewers, static and dynamic, with a document written
to be attacked rather than agreed with (`_cmp\HANDOFF-CROSS-CHECK.md`). It worked: **2 priority
claims were broken and 4 real defects were found in code written during round 1**, and one of my
environment statements was corrected in my favour.

| round-2 finding | my own verification | outcome |
|---|---|---|
| the synchronous read-back blocks forever on a non-pumping target, and the mutex with it | **reproduced**: with the target's UI thread suspended the shipped call was still blocked at 8 s (child process, killed); `SendMessageTimeoutW` returned in 1200 ms | defect 9 — fixed |
| `""` conflates a destroyed control with an empty one | **reproduced**: `_read_control_text(0x1234)` returned `''`, not `None` | defect 10 — fixed |
| `check-ctypes-names.py` cannot see a deleted prototype | **reproduced**: deleted `GetGUIThreadInfo.argtypes`, old checker exited **0** | defect 11 — fixed, and the new checker was itself shown failing |
| the `minimized` note explains non-Chromium windows with a Chromium mechanism | **reproduced independently**: own Notepad — 35 nodes minimized, 35 restored, note present both ways | defect 14 — the claim is fixed; the trigger is kept deliberately |
| Explorer degrades on every minimize, so "the variable is when the tree was built" holds for Chromium only | **reproduced here**, on an Explorer window this test opened itself (44 -> 8 -> 44; a never-queried minimized window is 8 too; **the raw view collapses to 8 as well**, so it is not a view-filter effect) | claim narrowed (B2) |
| `_is_read_only_control` applies `ES_READONLY` to every text-ish class | accepted from code reading; the bit is per-class by definition | defect 12 — fixed |
| the environment snapshot names the wrong Python | **refuted**: `ci-desktop-free.py` reports `python=3.12.10` under the documented interpreter — the reviewer's `python` resolves to 3.14.6, so the suite has now passed on **both** | no change needed; noted here as extra coverage |

Two things the review did **not** shake, both measured before they were relied on: that
`SendMessageTimeoutW`'s return value is a success flag rather than the result (verified on a real,
empty, cross-process `RICHEDIT50W`), and that its `lpdwResult` is pointer-sized — the first draft of
the fix used `w.DWORD` for it, which would have been an 8-byte write into a 4-byte buffer.

Still unmet from round 1: the B3 gate asked for "reason 1 independently reproduced on a different
Electron application". The reviewer could not satisfy it (the other Electron windows here expose
1–24 nodes or have equal named counts), so **`RawViewWalker` remains a one-application decision**.
