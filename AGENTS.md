# dsh-cua — agent 操作手册

> 这是一个 **Windows 优先**的 stdio MCP 服务器，把桌面当作可寻址对象暴露给 agent：
> UI Automation 元素寻址为主、坐标点击兜底，**并且会向正在用电脑的人让行**。
> 发布在 PyPI + 官方 MCP Registry + Glama + LobeHub。
>
> 本文件只放**每次任务都需要的东西**（命令、硬规则、文档索引），目标 150 行以内。
> 细节一律在 `..\dsh-devlog\docs\` 里，按下面的索引按需读。**不要把大段内容抄进本文件**——
> 单一事实源是这个项目最容易死的地方（见 `..\dsh-devlog\docs\lessons.md` 的"文档漂移"专题）。

> 🚦 **开工前（30 秒，三步 —— 别跳过第 2 步）**
>
> 1. **闸门**：`..\dsh-devlog\checks\check-agent-index.py` 与 `tests\check-all.py` 必须全绿才动手。
>    **空输出 / SKIP 都不算通过**——"什么也没做"和"通过了"在输出上无法区分。
> 2. **先问系统**：读 `..\dsh-devlog\docs\open-items.md`，确认这件事**有没有人已经做过、正在做、
>    或明确决定不做**。这一条挡的是两件真实发生过的事：**重复劳动**，以及**错过别人正在做的事**。
> 3. **收尾回写**：改完回头动 `..\dsh-devlog\docs\status.md`——它和 `open-items.md` 一样
>    **没有断言保护、会静默腐烂**。
>
> 更全的清单在 `..\dsh-devlog\docs\templates\agent-onboarding-prompt.md`：那份是写给"新 agent"的，
> 但它的**索引表**与**开工自检**对续做的 agent 一样适用。
> （这三步此前只写在那个模板里，靠"记得去读"兜——实测兜不住，所以搬到这里。）

## 1. 环境与命令

需要 **Windows** + **Python ≥ 3.10**（CI 跑 3.10 与 3.12）。桌面相关的测试需要**真实的交互式桌面会话**。

```powershell
# 安装（含测试依赖；缺 PyYAML 会让 check-workflow-shell.py 静默挂掉——这是踩过的坑）
python -m pip install ".[test]"

# 免桌面自检，全部应 exit 0（CI 里跑的就是这些）
python -m compileall -q src
python tests/check-workflow-shell.py            # workflow 里的 PowerShell 块能解析
python tests/check-ctypes-names.py              # 没有从错误的模块取 ctypes 名字
python tests/check-ctypes-prototypes.py         # 读回路径依赖的 Win32 原型都声明了
python tests/check-docs-consistency.py          # 文档与代码不打架（见第 4 节）
python tests/check-foreground-null.py           # 前台窗口为 NULL 时是拒绝，不是崩溃
python tests/check-handshake-classifier.py      # 握手回包分类器
python tests/ci-desktop-free.py                 # 免桌面主套件

# 上面这一整套用一条命令跑完，含每个 checker 的 --self-test；pre-commit 钩子跑的就是它
python tests/check-all.py

# 知识层侧（不在仓库里，所以只能手动跑）：两份入口文档的索引是否悬空、是否超行数/字节预算
python ..\dsh-devlog\checks\check-agent-index.py
```

**每个 checker 都要跑它的 `--self-test`**：一个从没见过失败的检查器，可能根本不会工作。
`check-all.py` 按文件名自动发现 `tests/check-*.py`，所以新加的 checker 不会被漏掉。

```powershell
# 需要真实桌面，必须在有交互式会话的机器上人工跑，CI 跑不了
python tests/verify-tools-readonly.py           # 逐个调用所有只读工具
python tests/verify-coexistence.py              # 会合成真实人类输入，会动光标
python tests/verify-visible-vs-foreground.py
python tests/verify-key-routing.py              # 70 KB，键盘投递路由普查
```

```bash
# 只能在 Linux 上跑（Windows 上会 SKIP）：证明非 Windows 也能启动并回应 tools/list
python tests/linux-handshake.py
```

## 2. 硬规则

1. **版本号在四个地方，必须同步**：`pyproject.toml`、`src/dsh_cua/__init__.py`、`server.json`（**两处**）。
   `check-docs-consistency.py` 会抓不一致。历史上 `serverInfo.version` 曾连续五个版本谎报 `1.28.1`。
2. **`server.json` 的 `description` ≤ 100 字符**（官方 registry schema 的 `maxLength`）。
   曾有版本写成 239 字符，workflow 在 tag 已经打出来之后才失败。
3. **改代码必须同步改 README**。README 宣称的工具数、`hard gate` 数量、安装命令、本地路径，
   全部被 `check-docs-consistency.py` 断言。历史上 README 曾说 5 个工具"硬门"而代码只有 2 处。
4. **不要在这里抢焦点——注意"软门"不等于"不抢前台"。**
   门控分两级：软门只取跨进程互斥锁；硬门（裸点击的 `raw_event` 路径、全局热键）**还会向人让行**
   （可观测的让行窗是 400 ms 级，只有硬门有）。录屏/演示只依赖硬门这一条。
   **但软门工具仍可能抬窗口**：`_ensure_visible`（`bridge.py:275`）会调 `SetForegroundWindow`
   （`:301`/`:315`），而它**不是输入事件**，`GetLastInputInfo` 测不到——这一点由 `arbiter.py:39` 自己写明。
   当前代码里"走软门但仍会尝试抬窗口"的至少有 `send_alt_key`（门 `:649`，抬窗 `:662`）与
   `type_text`（门 `:958`，抬窗 `:966`）。**用户正在用电脑时不要调它们**：最好情况是抬窗失败
   （Windows 前台锁下 `SetForegroundWindow` 常**静默失败**并返回 0），最坏情况是**把他正在看的窗口掀掉**。
   → 这是本项目对外宣称的差异点所在，也是已知未修项 **O02**（另有 D3：该函数的注释说"所有比较都经
   `_root()`"，但 `SetForegroundWindow`/`BringWindowToTop` 实际仍传原始子 hwnd）。
5. **临时产物写 `%TEMP%`**，不要落进仓库。发布产物曾因为沙箱给 `dist\` 的 ACL 掩码缺 `READ_CONTROL`
   而读不动，最后改成在 `%TEMP%` 重建。
6. **不要凭记忆办事**。README 与文档里的每条声明都要能指回代码/命令/发布物；做不到就标"未验证"。
7. **提交规则（用户 2026-09-28 授权，有条件常驻）**：免桌面检查器**全绿**时可以直接
   `git commit`，不必每次再问。做法是先跑 `python tests/check-all.py`（它会自动发现
   `tests/check-*.py` 并连带跑每个的 `--self-test`），本机还装了版本化钩子
   `.githooks/pre-commit`，提交时会自动跑同一条命令，红了不给提交。
   **但 `git push`、打 tag、发版这三件事仍然必须由用户明确要求**——这一点没有常驻授权。

## 3. 目录地图

```
src/dsh_cua/
  server.py      MCP 工具定义（19 个，全部 tool_ 前缀）与平台守卫
  bridge.py      49 KB：Win32 调用、门控接入、打字/读回、点击三级阶梯
  uia.py         54 KB：UI Automation 树遍历、元素寻址、离线裁剪
  arbiter.py     11 KB：共存策略——跨进程 named mutex + 人机让行
tests/           24 个文件：check-* 免桌面断言 + verify-* 真桌面核验
skill/computer-use/SKILL.md    随仓库分发的技能（工具怎么用）
.github/workflows/  ci.yml / publish.yml / publish-registry.yml
docs/            **不在本仓库里** —— 知识层故意留在 `..\dsh-devlog\docs\`（见第 4 节与下注）
```

## 4. 文档索引（做这类事之前先读）

| 你要做的事 | 先读 |
|---|---|
| **新 agent 接手本项目**（新会话、无上下文） | `..\dsh-devlog\docs\templates\agent-onboarding-prompt.md`（**整段复制粘贴即可；先读它，再读本文件**） |
| **在本机做窗口/GUI 自动化**（最高频的踩坑源） | `..\dsh-devlog\docs\environment-pitfalls.md` |
| 发新版本 | `..\dsh-devlog\docs\playbooks.md` → 发布链路 |
| 写交接单给外部 agent 独立审查 | `..\dsh-devlog\docs\templates/review-brief.md` |
| 搞清某个决定为什么这么定 / 被推翻过没有 | `..\dsh-devlog\docs\decisions.md` |
| 当前状态、还有什么没做完 | `..\dsh-devlog\docs\status.md`、`..\dsh-devlog\docs\open-items.md` |
| 排查一个可能已经踩过的坑 | `..\dsh-devlog\docs\lessons.md`（含"明确不算缺陷的"清单） |
| 硬事实速查（版本、渠道、文件、平台约束） | `..\dsh-devlog\docs\reference.md` |
| 这段历史的来龙去脉（DSH：4 天 / 174 轮） | `..\dsh-devlog\build\periods\period_01..08.md` |
| **换 harness 之前**的历史（ZCode，09-05 ~ 09-24） | `..\dsh-devlog\build\periods\period_pre01..08.md`；坑表 `..\dsh-devlog\docs\environment-pitfalls-zcode.md` |

> **为什么上面每条都写 `..\dsh-devlog\docs\` 而不是 `docs/`**：知识层**故意不进本仓库**。
> 本仓库是 **public**，而知识层里有审计发现、凭据卫生记录、以及作者自己被推翻的判断；
> 复制进来既会外泄内部材料，又会造成"每件事两份"——正是这套系统要防的那个失败。
> 所以：**只有 `tests/check-docs-consistency.py` 进仓库**（它是可提交的断言），其余一律留在 `dsh-devlog\docs\`。
> 🔴 **别"顺手"把 `dsh-devlog\docs\` 拷进仓库**——那会把这条路重新走一遍。
> 索引里凡是引用仓库内 `docs\` 下文件、而仓库里并没有这个目录时，**那就是悬空引用，按本注修正**。
> （这条由 `..\dsh-devlog\checks\check-agent-index.py` 机械断言，不用靠人记得。）
>
> 📌 **公开克隆者会发现上面那些 `..\dsh-devlog\` 全都跟不到 —— 这是预期的，不是待修的悬空引用。**
> 本文件随仓库公开，而知识层只存在于**维护者本机**（`dsh-cua` 与 `dsh-devlog` 是并列的两个仓库）。
> 克隆本仓库只会得到 `dsh-cua`，那些路径按设计就不存在。
> **不要**为了让它们"能点开"而改写或删掉它们——那等于把索引从它唯一有意义的语境里拿掉。
> 上一条的悬空判据说的是**本机**视角：在维护者的 checkout 里它们必须真实存在。

## 5. 本文件的维护约定

- **超过 200 行就是失败**：把内容挪进 `..\dsh-devlog\docs\`，这里只留一行索引。
- 如果你发现本文件某条与代码不符，**优先改代码或改这里的断言**，而不是绕过它——
  历史上"文档与实际不符"是这个项目最大的一类缺陷。
