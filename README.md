# dsh-cua

[![ci](https://github.com/Hutusion/dsh-cua/actions/workflows/ci.yml/badge.svg)](https://github.com/Hutusion/dsh-cua/actions/workflows/ci.yml)

Windows 电脑操控的 MCP 服务器 + agent 技能：**无障碍元素动作优先，截图只是兜底**；
带跨会话仲裁器——多个 agent 共享一台电脑时自动串行化，并在你正在使用电脑时主动让行。

本仓库只含 MCP 服务器与技能本身，**对任何 stdio MCP 客户端保持中立**
（dsh / Claude Code / Codex / Cursor / Cline / ZCode …）—— 不依赖 dsh 才能用。

## 它是什么

一个 stdio MCP 服务器，暴露 19 个工具：

- **观测**（只读，随时可调）：`skyshot`（把窗口读成带 diff 的紧凑文本树，比截图小三个数量级）、
  `element_at_point`、`read_element`、`find_elements`、`capture_window`（DPI 感知 + 客户区裁剪）、
  `list_windows` / `find_window` / `get_window_rect`、`list_displays`、`cursor_position`、
  `clipboard_read`、`coexistence_status`
- **元素动作**（软门：跨 agent 串行，无物理输入注入）：`element_action` /
  `element_action_at`——press / set_value / select / toggle / expand / collapse /
  scroll_into_view / focus，直接作用于 UIA 元素，**不抢焦点、不关心 z 序**
- **物理输入**（硬门：跨 agent 串行 + 人机让行）：`click_at`（先元素路径后裸事件）、
  `send_keys`、`type_text`（PostMessage 定向）、`clipboard_write`、`open_application`

每个动作返回**回执**而非自述成功：`action_sent` / `effect_verified` / `foreground_changed` /
`user-active` / `arbiter-busy`。「调用被接受」和「效果发生」是两件事——工具替 agent 分清。

## 与同类有何不同

Windows 侧已经有好几个成熟的开源实现。dsh-cua 的差异集中在**「和人类共用一台机器」**这一点上：

| | dsh-cua | [cua-driver](https://github.com/trycua/cua) | [ahk-mcp](https://github.com/anomalous3/ahk-mcp) | [lean-computer-use-mcp](https://github.com/Kvxw1105/lean-computer-use-mcp) |
|---|---|---|---|---|
| 元素动作走 UIA 模式（不抢焦点、不关心 z 序） | ✅ | ✅（ax 档） | ❌ 只有坐标点击 | 经 cua-driver |
| **检测到人正在输入 → 拒绝** | ✅ `user-active` | ❌ | ❌ | ❌ |
| 跨 agent 串行化（多进程） | ✅ named mutex | ❌ | ❌ | ❌ |
| 逐动作效果断言 | ✅ `effect_verified` 三态 | 报告交付档位 | ❌ | ❌ 仅 `state_changed` 启发式 |
| 抢前台副作用度量 | ✅ `foreground_changed` | ❌ | ❌ | ❌ |
| 工具数 | 19 | 59 | 15 | 6 |

**关键区别是两件常被混为一谈的事：**

- **「不抢焦点」是机制保证** —— 走 UIA 模式或定向 `PostMessage`，物理上不碰光标和键盘焦点。
  cua-driver 有（ax 档）；**ahk-mcp 其实没有** —— 它只有坐标点击，每次都移动真实光标。
- **「你一动就让路」是时间保证** —— 用 `GetLastInputInfo` 读人类最后一次输入的年龄，
  检测到你正在用就等待，超时则**拒绝**（`user-active`）而不是硬上。**上表另外三个实现里都没有这一条。**

`effect_verified` 同样是同类没有的：它把「调用被接受」和「效果发生」分开，给出三态
（`true` 变化符合预期 / `false` 接受了但没变并降级为失败 / `null` 无可比状态即未确认）。
同类的替代做法是动作后重新观察一次，把判断留给模型。

**dsh-cua 不做的事**（先说清楚，避免误解）：没有像素/视觉接地——树表达不了的界面（canvas、
游戏、远程桌面）用不了；没有录制回放；没有隔离沙箱。这些各有更合适的方案。

## 安装

需要 **Windows x64 + 交互式桌面会话 + Python ≥3.10**（或装好 uv 直接 uvx）。

```bash
# 方式一：uvx 零安装（推荐）
uvx dsh-cua                      # 直接运行 stdio MCP server

# 方式二：pip
pip install dsh-cua
dsh-cua-server

# 方式三：从源码
pip install git+https://github.com/Hutusion/dsh-cua.git
```

> 方式一/二要求 `dsh-cua` 已发布到 PyPI。**若 `uvx`/`pip` 报 404，改用方式三 —— 它总是可用。**
> 用源码方式时，接线里的 `command` 换成 `dsh-cua-server`（已装进 PATH）。

## 接线

任何 MCP 客户端，把 server 命名为 **`win32`**（skill 的工具名约定是 `mcp__win32__*`）：

```json
{ "mcpServers": { "win32": { "command": "uvx", "args": ["dsh-cua"] } } }
```

更多形状见 [`examples/`](examples/)：Claude Code / 通用客户端 / dsh 的 cordis.patch.yml 片段 /
想让模型看懂截图时所需的路由模态声明（`tr-route-settings.yml`）。

## 技能（可选但强烈建议）

[`skill/computer-use/SKILL.md`](skill/computer-use/SKILL.md) 是配套的使用教条：观察→定位→动作→复核
的循环、回执语义、重试安全、与人类共存的纪律。没有它模型也能用工具，但有了它模型会**自己选对
路径**——实测差别很大。把它复制进你的技能目录即可：

```bash
# Claude Code / 通用 agents
cp -r skill/computer-use ~/.agents/skills/
# dsh
cp -r skill/computer-use ~/.dsh/skills/
```

## 安全模型

| 级别 | 覆盖操作 | 门 |
|---|---|---|
| 只读 | 观测类 12 个工具 | 不进门，随时可调 |
| 软门 | 元素动作、PostMessage 打字、剪贴板写、启动应用 | 跨 agent 互斥锁（named mutex，多进程自动串行） |
| 硬门 | 裸点击、全局热键 | 互斥锁 + `GetLastInputInfo` 人机让行：用户最近有输入就等待，超时则拒绝 `user-active` 而非抢光标 |

诚实边界：让行是合作协议不是硬保证（注入前 150ms 紧检查已尽量收窄窗口）；
个别应用连 `set_value` 都会自激活（回执会如实报告 `foreground_changed`）；
同一窗口的「双人操作」没有技术解，别和 agent 同时操作同一个窗口。

## 测试

```bash
python tests/verify-coexistence.py    # 25 项：零输入证明 / 跨进程互斥 / 合成人机争用 / 杀开关
python tests/verify-p0-fixes.py       # 0.2.0 修掉的三个 P0：每项在修复前必失败
```

测试不需要真人配合——「用户输入」由一次真实 1px 光标移动合成，跑完还原。

**测试需要真实交互式桌面会话**（部分检查要创建窗口并用 UIA 寻址），所以**不能在 GitHub 托管的
runner 上跑**。CI 覆盖的是不需要桌面的那部分：打包安装、模块导入、diff 索引与树行转义的回归、
仲裁器的判定逻辑 —— 见 [`.github/workflows/ci.yml`](.github/workflows/ci.yml)。

```bash
python tests/ci-desktop-free.py       # 上面这些的本地等价物，不需要桌面
```

## License

MIT
