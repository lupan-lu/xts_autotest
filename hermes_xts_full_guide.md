# Hermes Agent 完整配置文档

> 版本：Hermes Agent v0.10.0 | 平台：macOS | 更新日期：2026-04-30

---

## 目录

1. [安装 Hermes Agent](#一安装-hermes-agent)
2. [模型提供商配置](#二模型提供商配置)
3. [接入 QQ Bot](#三接入-qq-bot)
4. [SOUL.md 人设配置](#四soulmd-人设配置)
5. [Skill：xTS 自动化测试助手](#五skill-xts-自动化测试助手)
6. [Skill：Android 全栈问题诊断](#六skill-android-全栈问题诊断)
7. [test_config.yaml 配置模板](#七test_configyaml-配置模板)
8. [Gateway 服务管理](#八gateway-服务管理)
9. [QQ 对话命令速查](#九qq-对话命令速查)
10. [踩坑记录与注意事项](#十踩坑记录与注意事项)

---

## 一、安装 Hermes Agent

```bash
# 一键安装
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash

# 验证
hermes --version
# Hermes Agent v0.10.0 (2026.4.16)
```

> 如果 `hermes` 命令找不到：
> ```bash
> echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc
> ```

### 目录结构

```
~/.hermes/
├── config.yaml          # 主配置文件
├── .env                 # 环境变量（API Key 等）
├── SOUL.md              # 机器人人设
├── skills/              # 自定义 Skill
│   ├── xts-automation/
│   └── android-fullstack-engineer/
├── hermes-agent/        # 框架源码（自动安装）
└── logs/                # 运行日志
    ├── gateway.log
    ├── agent.log
    └── errors.log
```

---

## 二、模型提供商配置

```bash
hermes setup
```

### 推荐方案：OpenCode Go

| 项目 | 值 |
|------|-----|
| Provider | `opencode-go` |
| Base URL | `https://opencode.ai/zen/go/v1` |
| Model | `kimi-k2.6` |
| 获取 Key | [opencode.ai/auth](https://opencode.ai/auth) |

### .env 必填变量

```bash
# ~/.hermes/.env

# 模型 API Key（必填）
OPENCODE_GO_API_KEY=<YOUR_KEY>

# QQ Bot（接入 QQ 时必填）
QQ_APP_ID=<YOUR_APP_ID>
QQ_CLIENT_SECRET=<YOUR_APP_SECRET>
QQ_ALLOW_ALL_USERS=true

# 可选
FIRECRAWL_API_KEY=<YOUR_KEY>       # 网页抓取
GEMINI_API_KEY=<YOUR_KEY>          # Gemini TTS
```

---

## 三、接入 QQ Bot

### 1. 注册 QQ Bot

1. 访问 [q.qq.com](https://q.qq.com) → 注册开发者 → 创建机器人
2. 记录 **App ID** 和 **App Secret**
3. 开启 intents：`C2C_MESSAGE_CREATE`（私聊）+ `GROUP_AT_MESSAGE_CREATE`（群 @）
4. 先设为**沙箱模式**测试

### 2. 交互式配置

```bash
hermes gateway setup
# 选择 QQ Bot → 输入 App ID / Secret → DM 授权选 Allow all
```

### 3. 启用工具集

```bash
hermes tools
# 选择 Configure 💬 QQBot → 全选启用所有工具
```

### 4. 关键：确认 config.yaml 中 qqbot 工具集

> [!CAUTION]
> `hermes-qqbot` 基础适配器**必须在列表中**，否则 QQ Bot 无法响应任何消息和 Skill。

```yaml
# ~/.hermes/config.yaml
platform_toolsets:
  qqbot:
  - hermes-qqbot      # ← 必须有！缺少则 QQ Bot 完全不工作
  - browser
  - code_execution
  - cronjob
  - delegation
  - file
  - image_gen
  - memory
  - messaging
  - session_search
  - skills             # ← 必须有！否则 Skill 无法触发
  - terminal           # ← 必须有！否则无法执行 shell 命令
  - todo
  - tts
  - vision
  - web
```

### 5. 图片生成（可选）

```yaml
# ~/.hermes/config.yaml
image_gen:
  provider: openai
  model: gpt-image-2
  base_url: https://code.newcli.com/gptapi/v1
  api_key: <YOUR_OPENAI_KEY>
```

---

## 四、SOUL.md 人设配置

`~/.hermes/SOUL.md` 定义机器人的行为指令，**每条消息自动加载**，不需要重启 gateway。

```markdown
# Hermes Agent Persona

你是一个有用的 AI 助手，使用简体中文回复。

## xTS 自动化测试指令

当用户提到 **测试、test、run、跑** 等关键词，并包含
**CTS/VTS/GTS/STS/TVTS/GSI** 或以 `Cts`/`Vts`/`Gts`/`Sts`/`Tvts`/`vts_`
开头的模块名时，你必须：

1. **立即回复用户**：`🚀 收到！正在准备测试环境...`
2. **调用 xts-automation skill**
3. 读取配置文件：`cat <工作目录>/tools/test_config.yaml`
4. 执行 `adb devices` 检测设备
5. 根据模块名前缀自动匹配对应的工具
6. 构造并执行 tradefed 命令
7. 测试失败时收集 results 和 logs 最新文件夹并打包

**绝对不要说"找不到测试工具"**，所有工具路径都在配置文件中定义。
```

> [!IMPORTANT]
> SOUL.md 中的配置文件路径需要根据实际机器修改。

---

## 五、Skill：xTS 自动化测试助手

### 文件位置

```
~/.hermes/skills/xts-automation/
└── SKILL.md
```

### 核心能力

| 功能 | 说明 |
|------|------|
| 模块自动映射 | 根据模块名前缀（Cts/Vts/Gts/Sts/Tvts）自动选择 tradefed 工具 |
| 设备检测 | `adb devices` 自动识别，多设备时让用户选择 |
| 模块级/方法级测试 | 支持 `-m` 整个模块和 `-t` 单个测试方法 |
| 批量测试 | 一次给多个模块名，逐个执行并汇总结果 |
| 失败日志收集 | 自动从 results/logs 目录收集最新文件夹并打包 |
| 设备巡检 | 每 5 分钟 `adb devices` 检查设备在线状态 |
| 完成判定 | 基于终端输出文本标志（`End of Results`），不依赖进程退出或文件夹存在 |

### 模块名 → 工具映射

| 模块名前缀 | tool_key | 测试命令 |
|-----------|----------|---------|
| `Cts*` | `cts` | `run cts -m <module>` |
| `Cts*`（用户指定 gsi） | `cts-on-gsi` | `run cts-on-gsi -m <module>` |
| `Vts*` / `vts_*` | `vts` | `run vts -m <module>` |
| `Gts*` | `gts` | `run gts -m <module>` |
| `Sts*` / `sts_*` | `sts` | `run sts -m <module>` |
| `Tvts*` (full) | `tvts-full` | `run tvts-full-cert -m <module>` |
| `Tvts*` (maint) | `tvts-maint` | `run tvts-maint-cert -m <module>` |

### 测试完成判定标志

```
=============== Results ===============        ← 结果开始
=============== Summary ===============
Total Tests      : 561
PASSED           : 29
FAILED           : 2
============== End of Results ==============   ← ⭐ 测试执行结束
============ Result/Log Location ============
LOG DIRECTORY    : <path>
RESULT DIRECTORY : <path>
=================== End ====================   ← ⭐ 一切结束
```

> [!NOTE]
> 当出现 `End of Results` 但 `done="false"` 时，属于 tradefed 已知问题，视为测试已完成。

### 用户交互示例

```
测试 CtsMediaTestCases                                    → 模块级测试
测试 CtsMediaTestCases -t MediaPlayerTest#testPlayAudio   → 方法级单测
用 gsi 测试 CtsOsTestCases                                → CTS-on-GSI
vts 测试 vts_kernel_test                                   → VTS
tvts full 测试 TvtsDeviceInfoTests                         → TVTS full-cert
跑一下 CtsMediaTestCases 和 CtsNetTestCases               → 批量测试
```

---

## 六、Skill：Android 全栈问题诊断

### 文件位置

```
~/.hermes/skills/android-fullstack-engineer/
├── SKILL.md
└── references/
    └── android-fullstack-playbook.md    # 完整规则手册（396 行）
```

### 来源

从 `~/Workspace/skills/android-fullstack-engineer/` 迁移，同时存在于 Claude Code（`~/.claude/skills/`）。

### 核心能力

| 工作模式 | 说明 |
|---------|------|
| 需求与方案模式 | 分层设计、职责划分、风险清单 |
| 代码与架构分析 | 识别耦合、并发、内存、异常处理问题 |
| 代码编写与重构 | 遵循现有风格，增量改造 |
| 日志与故障排查 | logcat/dmesg/tombstone/ANR 根因分析 |
| 平台移植与 HAL 联调 | dts/kconfig/rc/selinux/AIDL-HIDL |
| 脚本与工具 | Shell/Python 自动化脚本 |
| 文档与复盘 | 排查复盘 + 防再发措施 |

### 分层决策树

```
logcat Java 异常 / StrictMode     → App / Framework
tombstone / SIGSEGV / SIGABRT     → Native / HAL / JNI
dmesg oops / panic / IRQ 异常     → Kernel / 驱动
权限/接口不可用                     → App 权限 → SELinux → HAL → 驱动节点
```

### 与 xts-automation 的分工

| Skill | 触发 | 职责 |
|-------|------|------|
| `xts-automation` | "测试 CtsXxx" | **执行**测试、收集日志 |
| `android-fullstack-engineer` | "分析日志" / 粘贴 stack trace | **诊断**问题、给修复建议 |

---

## 七、test_config.yaml 配置模板

路径：`<工作目录>/tools/test_config.yaml`

```yaml
tools:
  cts:
    tradefed: /path/to/android-cts/tools/cts-tradefed
    run_command: "run cts"
    results_dir: /path/to/android-cts/results
    logs_dir: /path/to/android-cts/logs
    output_dir: /path/to/Reports/Cts

  cts-on-gsi:
    # 与 CTS 共用 tradefed
    tradefed: /path/to/android-cts/tools/cts-tradefed
    run_command: "run cts-on-gsi"
    results_dir: /path/to/android-cts/results
    logs_dir: /path/to/android-cts/logs
    output_dir: /path/to/Reports/CtsOnGsi

  vts:
    tradefed: /path/to/android-vts/tools/vts-tradefed
    run_command: "run vts"
    results_dir: /path/to/android-vts/results
    logs_dir: /path/to/android-vts/logs
    output_dir: /path/to/Reports/Vts

  gts:
    tradefed: /path/to/android-gts/tools/gts-tradefed
    run_command: "run gts"
    results_dir: /path/to/android-gts/results
    logs_dir: /path/to/android-gts/logs
    output_dir: /path/to/Reports/Gts

  tvts-full:
    tradefed: /path/to/android-tvts/tools/tvts-tradefed
    run_command: "run tvts-full-cert"
    results_dir: /path/to/android-tvts/results
    logs_dir: /path/to/android-tvts/logs
    output_dir: /path/to/Reports/Tvts

  tvts-maint:
    tradefed: /path/to/android-tvts/tools/tvts-tradefed
    run_command: "run tvts-maint-cert"
    results_dir: /path/to/android-tvts/results
    logs_dir: /path/to/android-tvts/logs
    output_dir: /path/to/Reports/Tvts

  sts:
    tradefed: /path/to/android-sts/tools/sts-tradefed
    run_command: "run sts"
    results_dir: /path/to/android-sts/results
    logs_dir: /path/to/android-sts/logs
    output_dir: /path/to/Reports/Sts
```

> [!TIP]
> 所有路径需根据新机器的实际 XTS 工具存放位置修改。

---

## 八、Gateway 服务管理

```bash
# 前台测试（先确认 QQ Bot 正常）
hermes gateway

# 安装为 launchd 后台服务
hermes gateway install

# 服务管理
hermes gateway start      # 启动
hermes gateway stop       # 停止
hermes gateway status     # 查看状态

# 重启（修改 config.yaml 后需要）
hermes gateway stop && hermes gateway start
```

> [!NOTE]
> SOUL.md 修改后**不需要重启** gateway，每条消息自动重新加载。
> config.yaml 修改后**需要重启** gateway。

### 日志排查

```bash
tail -f ~/.hermes/logs/gateway.log        # 实时 gateway 日志
tail -f ~/.hermes/logs/agent.log          # agent 执行日志
cat ~/.hermes/logs/errors.log             # 错误日志
```

---

## 九、QQ 对话命令速查

### 会话管理

| 命令 | 作用 |
|------|------|
| `/new` | 新建空白会话 |
| `/title <名称>` | 给当前会话命名 |
| `/resume <名称>` | 切回已命名的会话 |
| `/compress` | 压缩当前上下文（过长时使用） |
| `/undo` | 撤销上一轮对话 |
| `/retry` | 重试上一条消息 |
| `/clear` | 清屏并开始新会话 |

### 控制命令

| 命令 | 作用 |
|------|------|
| `/commands` | 查看所有可用命令 |
| `/stop` | 终止当前运行中的任务 |
| `/background <prompt>` | 在后台执行任务 |
| `/model <name>` | 切换模型 |
| `/status` | 查看当前会话状态 |

---

## 十、踩坑记录与注意事项

### 1. QQ Bot 不响应消息

> [!WARNING]
> 最常见原因：`config.yaml` 中 `qqbot` 工具集缺少 `hermes-qqbot` 基础适配器。

**解决**：确认 `platform_toolsets.qqbot` 列表首项是 `hermes-qqbot`，然后重启 gateway。

### 2. Skill 不被触发

**原因**：Hermes 的 Skill 语义匹配可能不够准确。
**解决**：在 SOUL.md 中显式注入触发指令（如 xTS 测试指令），确保 agent 每条消息都能看到。

### 3. `hermes gateway stop` 报 "Could not find service"

**正常现象**。stop 时 launchd job 被完全卸载，start 时会自动重新加载。只要最后显示 `✓ Service started` 就没问题。

### 4. tradefed `done="false"` 但测试已完成

**tradefed 已知问题**。某些模块因软件缺陷导致 `done` 永远为 `false`。
**判定方式**：以终端输出 `End of Results` + `=================== End ==` 为准。

### 5. macOS 上 Java 环境

新版 CTS/VTS 需要 Java 21。macOS 上 jenv 需要每次手动激活：
```bash
eval "$(jenv init -)" && jenv global 21 && java -version
```

### 6. 新版 CTS (16_r4+) 使用 OmniLab

行为与老版 tradefed 不同：
- 启动后进入 `ats-console`，异步执行
- 必须用 `background=true` 启动
- 日志在 `/tmp/ats_console_log/` 和 `<suite_root>/logs/`

---

## 快速部署检查清单

在新机器上完成安装后逐项确认：

- [ ] `hermes --version` 输出版本号
- [ ] `~/.hermes/.env` 已填写 OPENCODE_GO_API_KEY / QQ_APP_ID / QQ_CLIENT_SECRET
- [ ] `~/.hermes/config.yaml` 中 qqbot toolset 包含 `hermes-qqbot` + `skills` + `terminal`
- [ ] `~/.hermes/SOUL.md` 包含 xTS 测试指令（路径已改为本机）
- [ ] `~/.hermes/skills/xts-automation/SKILL.md` 存在
- [ ] `~/.hermes/skills/android-fullstack-engineer/SKILL.md` + `references/` 存在
- [ ] `hermes skills list` 显示 `xts-automation` 和 `android-fullstack-engineer`
- [ ] `test_config.yaml` 路径已根据本机调整
- [ ] `hermes gateway` 前台运行后，QQ 发消息能收到回复
- [ ] QQ 发"Vts测试：vts_eol_enforcement_test"触发 xts-automation skill
- [ ] `hermes gateway install && hermes gateway start` 后台服务正常
