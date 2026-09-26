# dsh-cua

[![ci](https://github.com/Hutusion/dsh-cua/actions/workflows/ci.yml/badge.svg)](https://github.com/Hutusion/dsh-cua/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/dsh-cua)](https://pypi.org/project/dsh-cua/)
[![Hutusion/dsh-cua MCP server](https://glama.ai/mcp/servers/Hutusion/dsh-cua/badges/score.svg)](https://glama.ai/mcp/servers/Hutusion/dsh-cua)
[![LINUX DO](https://img.shields.io/badge/LINUX-DO-FFB003.svg?logo=data:image/svg%2bxml;base64,DQo8c3ZnIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyIgd2lkdGg9IjEwMCIgaGVpZ2h0PSIxMDAiPjxwYXRoIGQ9Ik00Ni44Mi0uMDU1aDYuMjVxMjMuOTY5IDIuMDYyIDM4IDIxLjQyNmM1LjI1OCA3LjY3NiA4LjIxNSAxNi4xNTYgOC44NzUgMjUuNDV2Ni4yNXEtMi4wNjQgMjMuOTY4LTIxLjQzIDM4LTExLjUxMiA3Ljg4NS0yNS40NDUgOC44NzRoLTYuMjVxLTIzLjk3LTIuMDY0LTM4LjAwNC0yMS40M1EuOTcxIDY3LjA1Ni0uMDU0IDUzLjE4di02LjQ3M0MxLjM2MiAzMC43ODEgOC41MDMgMTguMTQ4IDIxLjM3IDguODE3IDI5LjA0NyAzLjU2MiAzNy41MjcuNjA0IDQ2LjgyMS0uMDU2IiBzdHlsZT0ic3Ryb2tlOm5vbmU7ZmlsbC1ydWxlOmV2ZW5vZGQ7ZmlsbDojZWNlY2VjO2ZpbGwtb3BhY2l0eToxIi8+PHBhdGggZD0iTTQ3LjI2NiAyLjk1N3EyMi41My0uNjUgMzcuNzc3IDE1LjczOGE0OS43IDQ5LjcgMCAwIDEgNi44NjcgMTAuMTU3cS00MS45NjQuMjIyLTgzLjkzIDAgOS43NS0xOC42MTYgMzAuMDI0LTI0LjM4N2E2MSA2MSAwIDAgMSA5LjI2Mi0xLjUwOCIgc3R5bGU9InN0cm9rZTpub25lO2ZpbGwtcnVsZTpldmVub2RkO2ZpbGw6IzE5MTkxOSIgZmlsbC1vcGFjaXR5PSIxIi8+PHBhdGggZD0iTTcuOTggNzAuOTI2YzI3Ljk3Ny0uMDM1IDU1Ljk1NCAwIDgzLjkzLjExM1E4My40MjYgODcuNDczIDY2LjEzIDk0LjA4NnEtMTguODEgNi41NDQtMzYuODMyLTEuODk4LTE0LjIwMy03LjA5LTIxLjMxNy0yMS4yNjIiIHN0eWxlPSJzdHJva2U6bm9uZTtmaWxsLXJ1bGU6ZXZlbm9kZDtmaWxsOiNmOWFmMDA7ZmlsbC1vcGFjaXR5PSIxIi8+PC9zdmc+)](https://linux.do)

<!-- LINUX DO 徽章不是装饰：linux.do 的「开源推广」通道要求项目反向链接认可社区。
     见 https://linux.do/t/topic/1776670 -->

<!-- mcp-name: io.github.Hutusion/dsh-cua -->

[English](README.md) · **中文**

Windows 电脑操控的 MCP 服务器 + agent 技能：**无障碍元素动作优先，截图只是兜底**；
带跨会话仲裁器——多个 agent 共享一台电脑时自动串行化，并在你正在使用电脑时主动让行。

本仓库只含 MCP 服务器与技能本身，**对任何 stdio MCP 客户端保持中立**
（dsh / Claude Code / Codex / Cursor / Cline / ZCode …）—— 不依赖 dsh 才能用。

**平台语义（0.3.1 起）**：工具只在 Windows 上可用——它们驱动 user32/kernel32 与 UI Automation。
但**包本身在别的平台也能导入、服务器也能启动**，并照常回应 `tools/list`，所以任何客户端
或目录抓取器都能枚举到这 19 个工具及其完整 schema；真去调用某个工具时得到的是一句明确的
「需要 Windows」错误，而不是进程根本起不来。0.3.0 在导入时就抛错，导致这类抓取完全看不到它
（`tests/linux-handshake.py` 是这条性质的回归测试，CI 在 `ubuntu-latest` 上跑它）。

## 它是什么

一个 stdio MCP 服务器，暴露 19 个工具。工具名一律带 `tool_` 前缀，与 `tools/list` 返回的完全一致。

- **观测**（只读，随时可调）：`tool_skyshot`（把窗口读成带 diff 的紧凑文本树，比截图小三个数量级）、
  `tool_element_at_point`、`tool_read_element`、`tool_find_elements`、`tool_capture_window`（DPI 感知 + 客户区裁剪）、
  `tool_list_windows` / `tool_find_window` / `tool_get_window_rect`、`tool_list_displays`、`tool_cursor_position`、
  `tool_clipboard_read`、`tool_coexistence_status`
- **元素动作**（软门：跨 agent 串行，无物理输入注入）：`tool_element_action` /
  `tool_element_action_at`——press / set_value / select / toggle / expand / collapse /
  scroll_into_view / focus，直接作用于 UIA 元素，**不抢焦点、不关心 z 序**
- **其它写入型调用**（同样是软门：只拿互斥量，**不**做人机让行）：`tool_type_text`（PostMessage 定向）、
  `tool_clipboard_write`、`tool_open_application`。它们不合成物理输入，所以**不会**等你停手——
  而剪贴板写入仍会毁掉你上次复制的内容，用之前先声明。
- **物理输入**（硬门：跨 agent 串行 **且** 人机让行）：只有两样东西和你共用同一个光标与键盘——
  `tool_click_at` 的**裸事件路径**与 `tool_send_keys` 的全局热键。门禁会等机器进入输入静默，
  超时后以 `user-active` 拒绝，而不是和你抢光标。`tool_click_at` 会**先试元素路径**（`ax_press`），
  它不注入物理输入，因此只拿互斥量——回执里的 `method` 字段说明实际走了哪条。

这里的「只读」指不产生变更动作、不合成输入，所以别人正在用这台机器时也可以调。其中两个有值得知道的
副作用：`tool_capture_window` 会把截图写到磁盘（`save_path`，省略则写临时文件），`tool_skyshot`
会更新服务端的 diff 基线。

每个动作返回**回执**而非自述成功：`action_sent` / `effect_verified` / `foreground_changed` /
`user-active` / `arbiter-busy`。「调用被接受」和「效果发生」是两件事——工具替 agent 分清。

## 与同类有何不同

Windows 侧已经有好几个成熟的开源实现。dsh-cua 的差异集中在**「和人类共用一台机器」**这一点上：

| | dsh-cua | [cua-driver](https://github.com/trycua/cua) | [ahk-mcp](https://github.com/anomalous3/ahk-mcp) | [lean-computer-use-mcp](https://github.com/Kvxw1105/lean-computer-use-mcp) |
|---|---|---|---|---|
| 元素动作以 UIA 模式投递（不抢焦点、不关心 z 序） | ✅ | ✅（ax 档） | ❌ 用 UIA 读、靠坐标点击动作 | 经 cua-driver |
| **检测到人正在输入 → 拒绝** | ✅ `user-active` | ❌ | ❌ | ❌ |
| 跨 agent 串行化（多进程） | ✅ named mutex | ❌ | ❌ | ❌ |
| 逐动作效果断言 | ✅ `effect_verified` 三态 | 报告交付档位 | ❌ | ❌ 仅 `state_changed` 启发式 |
| 抢前台副作用度量 | ✅ `foreground_changed` | ❌ | ❌ | ❌ |
| 工具数 | 19 | 59 | 15 | 6 |

**关键区别是两件常被混为一谈的事：**

- **「不抢焦点」是机制保证** —— 走 UIA 模式或定向 `PostMessage`，物理上不碰光标和键盘焦点。
  cua-driver 有（ax 档）。**ahk-mcp 没有**，但这里的区别比「没有 UIA」窄：它确实用 UIA 读取
  （`ahk_uia_tree` / `ahk_uia_find` / `ahk_uia_url`），只是没有 UIA **模式动作** ——
  按其 README，它靠坐标点击或合成按键动作，所以动作会移动真实光标。
- **「你一动就让路」是时间保证** —— 用 `GetLastInputInfo` 读人类最后一次输入的年龄，
  检测到你正在用就等待，超时则**拒绝**（`user-active`）而不是硬上。截至 2026-09-25，对上表
  另外三个代码库做模式检索未发现等价实现 —— 这是检索证据而非证明，且只覆盖「输入年龄检测」：
  cua-driver 另有不同类型的人机保护（需人类同意的授权门、前台抢焦检测与恢复）。

`effect_verified` 同样是同类没有的：它把「调用被接受」和「效果发生」分开，给出三态
（`true` 变化符合预期 / `false` 接受了但没变并降级为失败 / `null` 无可比状态即未确认）。
同类的替代做法是动作后重新观察一次，把判断留给模型。

**dsh-cua 不做的事**（先说清楚，避免误解）：它自己不做接地——服务器不分析像素，所以**纯文本
模型**走不了树表达不了的界面（canvas、游戏、远程桌面）。**模型有视觉时，像素通路是完整支持的**：
`capture_window` 在返回图片的同时给出经过校验的「图像坐标 → 屏幕坐标」映射（`bounds`、
`scale`、`dpi_verified`），接地由模型完成。另外没有录制回放；没有隔离沙箱。这些各有更合适的方案。

## 安装

需要 **Windows x64 + 交互式桌面会话 + Python ≥3.10** 才能真正操控桌面。
（包在 Linux/macOS 上同样可以安装与启动，`tools/list` 正常返回，只是调用工具时会明确
报「需要 Windows」——见上文"平台语义"。）

```bash
# 方式一：uvx 零安装（推荐）
uvx dsh-cua                      # 直接运行 stdio MCP server

# 方式二：pip
pip install dsh-cua

# 方式三：从源码
pip install git+https://github.com/Hutusion/dsh-cua.git
```

三种方式装完后，**用 `python -m dsh_cua` 起服务**：

```bash
python -m dsh_cua                # 不依赖 PATH 上的任何可执行文件
```

> **为什么不写 `dsh-cua-server`**：pip 会把 console script 装进解释器的 `Scripts` 目录，
> 而**那个目录不一定在 PATH 上** —— 实测 stock python.org 3.12 的 User 与 Machine PATH
> 都不含它，于是 `pip install dsh-cua` 成功、`dsh-cua-server` 却报 command not found。
> `python -m` 不需要任何 PATH 条目。console script 仍然提供，PATH 里有它时可用。
>
> 方式一/二现在都可用：包已发布在 PyPI（<https://pypi.org/project/dsh-cua/>）。
> 若哪一天 `uvx`/`pip` 报 404，用方式三 —— 它总是可用。

## 接线

任何 MCP 客户端，把 server 命名为 **`win32`**（skill 的工具名约定是 `mcp__win32__*`）。

**`python -m`（不依赖 PATH，推荐）**：

```json
{ "mcpServers": { "win32": { "command": "python", "args": ["-m", "dsh_cua"] } } }
```

**`uvx`（PyPI 发布后）**：

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
