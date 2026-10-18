# 中州韵输入法安装器 · 需求文档

> 版本：v1.0-draft ｜ 日期：2026-10-12 ｜ 状态：已确认，待开发
> 本文档是项目开发的唯一需求依据，后续变更在此维护。

## 1. 项目概述

一款 PyQt6 图形界面的「中州韵（RIME）输入法安装器」，帮助用户在 **Ubuntu 26.04+ / Deepin 25.2+** 上完成 RIME 引擎与输入法方案的安装、配置、卸载全生命周期管理。支持 **X11 与 Wayland** 两种显示协议。界面要求漂亮、大方、友好。

## 2. 技术栈与形态

| 项 | 决策 |
|---|---|
| 开发语言 | Python 3 + PyQt6 |
| UI 框架 | PyQt6-Fluent-Widgets（Fluent 设计，深浅色，i18n 预留） |
| 发行形态 | .deb 安装包；aria2 二进制随包；依赖系统 python3-pyqt6 |
| 下载内核 | aria2-next（取自 rayburst-main/src-tauri/binaries/，x86_64 + aarch64 双架构）+ 其 aria2.conf 默认参数 |
| 方案管理 | 内置 vendored plum（东风破，github.com/rime/plum），显式传 rime_frontend / rime_dir |
| 提权方式 | pkexec（apt 安装/卸载、系统级环境变量修复） |
| 数据持久化 | SQLite（manifest + 操作日志）+ 快照 zip |
| 版本号 | 统一定义在 config.py，每次功能落地 bump |

## 3. 功能需求

### P0 核心需求（原始需求）

| # | 功能 | 说明 |
|---|---|---|
| F1 | 环境检测 | 检测 OS/版本、会话协议（X11/Wayland）、运行中的输入法框架、RIME 引擎安装状态、rime 用户目录位置、输入法环境变量（GTK/QT/XMODIFIERS） |
| F2 | 引擎安装 | apt 安装 ibus-rime / fcitx5-rime（pkexec 提权，非交互模式），带进度条与动画 |
| F3 | 引擎卸载 | apt purge + 清理 rime 用户目录（二次确认），保证环境出问题后能干净卸载重装 |
| F4 | 方案市场 | 每个方案一张卡片：名称、简单介绍、优势、适用人群、体积、来源；范围 = 官方 preset/extra/all + 热门第三方（雾凇拼音 iDvel/rime-ice、白霜 gaboolic/rime-frost 等）+ 高级用户自定义 user/repo URL 输入 |
| F5 | 方案安装 | aria2 多线程下载仓库 tarball（国内加速）→ 解压预置到 plum package 缓存 → 执行 rime-install → manifest 记录引入/覆盖的文件 |
| F6 | 方案卸载 | 基于 manifest 删除该方案引入的文件、还原被覆盖文件的备份 |
| F7 | 按钮状态机 | 前置条件满足才可点击（如先装引擎才可装方案）；任何安装/卸载进行中全部禁用；引擎未装时方案安装入口置灰并提示原因 |
| F8 | 进度与日志 | 所有操作（apt/aria2/plum）经 QProcess 驱动；确定进度条（aria2 stdout 解析）+ 不定式动画（apt）；实时日志面板 + 日志文件落盘 + 一键导出 |
| F9 | 双协议支持 | Qt6 wayland→xcb 自动回退；按会话类型与框架自动选择推荐前端，允许手动切换 |

### P1 新增需求（已确认加入）

| # | 功能 | 说明 |
|---|---|---|
| F10 | 一键环境体检与修复 | 检测显示协议/框架进程/环境变量/部署状态并评分；自动修复（写 ~/.xprofile 或 /etc/environment，pkexec 提权），针对 Deepin 社区高频"装了用不了"问题 |
| F11 | 快照备份 / 恢复 / 导出 | 安装前自动快照 rime 用户目录为 zip；支持一键恢复与导出分享 |
| F12 | 部署后引导 | 一键 ibus restart / fcitx5 -r 重启框架；提供 ibus-setup / fcitx5-configtool 图形入口与添加输入法指引 |
| F13 | apt 镜像测速与切换 | 阿里/清华/中科大等镜像延迟测速，一键切换（备份原 sources），与 aria2 构成国内加速闭环 |
| F14 | 诊断包导出 | 一键导出 zip：运行日志 + 环境检测结果 + manifest + 系统信息，便于自证与求助 |
| F15 | 词库 / 皮肤增强包 | 作为可选组件与方案解耦安装（如雾凇词库、皮肤配置） |
| F16 | 方案更新检查 | 基于 plum 的 git pull 能力，检测并更新已安装的方案包 |

## 4. 关键设计决策（架构铁律）

1. **plum 只能装、不能卸**：install-packages.sh 仅拷贝覆盖、无删除、不留备份 → 卸载能力由安装器自建 manifest（SQLite：package / file / backup / operation 四表）+ 装前快照实现。
2. **国内加速路径**：aria2 下载 codeload tarball → 解压预置到 `plum/package/<user>/<name>/` → rime-install（plum 对非 git 包目录仅警告后跳过更新，不影响安装）；git url rewrite 为兜底。
3. **rime 用户目录随前端而变**：ibus-rime → ~/.config/ibus/rime；fcitx5-rime → ~/.local/share/fcitx5/rime；fcitx-rime → ~/.config/fcitx/rime。Deepin（fcitx5）必须显式指定，plum 在 Linux 默认猜 ibus。
4. **前端策略**：自动检测 + 可手动切换。
5. **发行形态**：.deb，aria2 二进制随包。
6. plum 不触发部署，靠框架文件监控；安装器提供重启框架兜底（F12）。

## 5. 执行计划（阶段 + 版本）

| 阶段 | 内容 | 版本 |
|---|---|---|
| ~~Phase 0~~ ✅ | 工程骨架：目录结构、config.py、aria2/plum 资源内置、deb 打包脚本、offscreen 冒烟 | v0.1.0 |
| ~~Phase 1~~ ✅ | 环境检测服务 + 主窗口 + 状态机（F1/F7/F9） | v0.2.0 |
| ~~Phase 2~~ ✅ | 引擎安装/卸载（F2/F3） | v0.3.0 |
| ~~Phase 3~~ ✅ | 方案市场 + aria2 加速安装 + manifest 卸载 + 更新检查（F4/F5/F6/F16） | v0.4.0 |
| ~~Phase 4~~ ✅ | 快照备份 / 恢复 / 导入导出 zip（F11/F14） | v0.5.0 |
| ~~Phase 5~~ ✅ | 体检修复 + 部署引导 + 镜像切换 + 词库增强包（F10/F12/F13/F15；词库作为方案市场 addon 分类落地，皮肤部分未做，留待后续） | v0.6.0 |
| Phase 6（进行中） | 深浅色主题 ✅ 动效打磨 ✅ i18n 框架+英文包 ✅（6c 主干 + 6c-2 按钮/反馈/分类/筛选/对话框；方案卡片简介与 tooltip 等仍回退中文）；F15 皮肤 ✅（6d：三套内置配色，标记块写入 default.custom.yaml + 重新部署，卸载只移除块不整文件还原，皮肤卡隐藏检查更新、应用/移除语义、中英文案）；双系统真机验证 待办 | v1.0.0 |
| 热修复 | 装机启动冻结修复（见下） | v1.0.1 |
| 热修复 v1.0.2 | 启动加固：GL 软渲染规避 + launcher 三级重试 + 启动标记；图标入包（见下） | v1.0.2 |
| 热修复 v1.0.3 | 装机秒崩修复：Depends 补 python3-pyqt6.qtsvg（见下） | v1.0.3 |
| 热修复 v1.0.4 | 首页体检英文标签修复 + 运行期卡死取证仪表化（GUI 看门狗/运行痕迹/长步骤日志，见下） | v1.0.4 |
| 热修复 v1.0.5 | 真机反馈修复：引擎安装多次密码框合并为单次提权 + apt 降优先级（nice/ionice）缓解安装期系统卡死；删除窗口悬停状态弹窗；环境变量误报修正（未设定不再报异常）；方案市场/体检页滚动区独享剩余高度；体检页导航 routeKey 碰撞修复（见下） | v1.0.5 |
| 热修复 v1.0.6 | 「装了引擎和方案却用不了输入法」闭环：重新部署升级为 rime_deployer 主动编译；新增 GNOME 输入源注册检查与一键修复；体检页 QLayout 泄漏导致文字重叠修复（见下） | v1.0.6 |
| 热修复 v1.0.7 | 窗口竖条/启动转圈/卡片错位/皮肤扩充与生效范围说明/默认框架误报（见下） | v1.0.7 |
| 热修复 v1.0.8 | fcitx/Deepin 专项：fcitx5 输入法列表注册检查、共享数据目录运行时探测、皮肤生效范围文案覆盖 fcitx5（见下） | v1.0.8 |

## 6. 验收标准

- Ubuntu 26.04 与 Deepin 25.2 实测：全新安装、方案安装、方案卸载、引擎卸载、干净重装全链路通过；
- 卸载后无残留文件（除用户明确保留的配置）；
- X11 与 Wayland 会话下安装器自身与安装后的输入法均正常；
- 开发期用 QT_QPA_PLATFORM=offscreen 做断言式冒烟验证。

## 7. 热修复记录（v1.0.1，2026-10-14）

**现象**：Ubuntu 26.04 安装 deb 后，点击图标无窗口出现，且系统卡死只能强制重启。

**根因**：
1. F9「Qt6 wayland→xcb 自动回退」在 Phase 1 标记完成但实际未实现。Ubuntu 26.04 默认 GNOME + Wayland，应用走 Qt6 wayland 平台插件，在部分 GPU/驱动组合下挂死合成器/GPU，导致整机会话冻结；窗口从未映射，表现为「点了图标没反应」。
2. 启动期无任何诊断留痕：Terminal=false 下 stderr 不可见，进程秒退时用户与开发者都看不到原因。

**修复**：
- 新增 `core/platform.py`：QApplication 创建前选择平台——Wayland 会话且 XWayland 可用（DISPLAY + X socket/Xwayland 进程）时强制 `QT_QPA_PLATFORM=xcb`；用户显式设置的 `QT_QPA_PLATFORM` 与逃生阀 `RIME_INSTALLER_FORCE_PLATFORM` 优先；纯 Wayland（无 XWayland）不干预。
- `main.py` 启动崩溃兜底：异常写入 `~/.local/share/rime-installer/logs/startup-crash-*.log`（含平台决策与环境变量），并尽量弹错误框。
- `packaging/build-deb.sh`：launcher 将 stderr 重定向到日志目录（超 1MB 截断），解释器/依赖级崩溃也可留痕。
- 冒烟新增 `check_platform()`：七种场景断言（默认不干预/尊重用户设置/逃生阀/Wayland→xcb/无 XWayland 不干预/决策说明/offscreen 尊重）。

## 7-2. 热修复记录（v1.0.2，2026-10-14）

**现象**：v1.0.1 装机后点击图标仍「没法点开」（窗口不出现），严重时系统卡死。

**根因分析**（v1.0.1 已排除 vendor qfluentwidgets 与 PyQt6 6.10.2 兼容性后，剩余高危路径）：
1. v1.0.1 强制 `QT_QPA_PLATFORM=xcb` 但目标机 xcb 插件缺失/损坏（qt6-qpa-plugins 未装上）时 Qt 直接 abort（"no Qt platform plugin could be initialized"），秒退无窗口；
2. 纯 Wayland 无 XWayland 时走 wayland 插件 EGL 直连 GPU，部分驱动组合挂死 GPU → 系统冻结；
3. xcb 路径下 GL 集成在部分驱动组合下同样会挂死 GPU；
4. 启动即崩时用户侧无任何可见反馈，也无法区分「秒退」与「窗口显示过但被关闭」。

**修复**：
- `core/platform.py`：xcb 回退分支追加 `QT_XCB_GL_INTEGRATION=none`（关闭 GL 集成）；无 XWayland 的 Wayland 分支追加 `LIBGL_ALWAYS_SOFTWARE=1`（软渲染规避 EGL 直连 GPU）；新增逃生阀 `RIME_INSTALLER_HW_GL=1` 豁免两者；`platform_decision_note()` 文案同步反映软渲染/GL 决策；用户显式 `QT_QPA_PLATFORM` 与 offscreen 依旧优先尊重。
- `main.py`：新增模块级 `write_launch_marker()`，`window.show()` 后、`app.exec()` 前向 `config.LAUNCH_OK_MARKER`（~/.local/share/rime-installer/launch-ok）写 ISO 时间戳——标记存在即说明窗口成功显示过（含用户主动关闭）。
- `config.py`：`VERSION = "1.0.2"`，新增 `LAUNCH_OK_MARKER`。
- `core/theme.py`：`resolve_mode()` 的 auto 解析改用自带 timeout=2s 的 subprocess 探测 gsettings（color-scheme，fallback gtk-theme）；QA 回归发现 darkdetect 回退路径的 gsettings 调用无超时（挂起时两次调用各阻塞约 30s，实测 62s），改为探测失败直接按浅色、不再回退 darkdetect，并删除 theme.py 的 darkdetect 导入；`apply_theme()` 的 AUTO 不再透传 `Theme.AUTO` 给 vendor（其 theme setter 同样会调无超时的 darkdetect），改为传入启动时解析出的具体明暗；任何失败按浅色，不抛异常。
- `packaging/build-deb.sh`：launcher 重写为三级重试链——第 1 次默认环境启动（启动前删旧标记），退出码为 0 或 launch-ok 标记存在即成功；否则第 2 次 `QT_QPA_PLATFORM=xcb`；仍失败且为 Wayland 会话时第 3 次 `QT_QPA_PLATFORM=wayland LIBGL_ALWAYS_SOFTWARE=1`；最多 3 次，全部失败返回最后退出码；每次尝试记录标签/平台/软渲染/退出码/标记到 launcher.log（超 1MB 截断逻辑保留）。QA 回归发现标记路径分叉（launcher 按 XDG_DATA_HOME 推导、应用按 HOME 推导，设 XDG_DATA_HOME 后满跑 3 次重试），已改为 LOG_DIR/MARKER 均从 HOME 推导，与 config.USER_DATA_DIR 逐字一致。
- 图标入包：`packaging/icons/rime-installer-{48,64,128,256,512}.png` 安装到 `usr/share/icons/hicolor/<size>x<size>/apps/rime-installer.png`，desktop 文件新增 `Icon=rime-installer`。
- 冒烟扩展：`check_platform()` 新增 GL 变量断言与 `RIME_INSTALLER_HW_GL=1` 场景；新增 `check_launch_marker()`（标记生成 + ISO 时间可解析 + 清理）与 `check_launcher_script()`（build-deb.sh 关键重试逻辑回归防线）。

**QA 回归发现并修复**（v1.0.2 回改后复审）：① theme 回退路径无超时——darkdetect 的 gsettings 调用无 timeout，挂起时阻塞启动约 60s（实测 62.03s），已改为探测失败直接按浅色、不再回退 darkdetect（core/theme.py）；② 启动标记路径 XDG 不一致——launcher 按 XDG_DATA_HOME 推导而应用按 HOME 推导，设 XDG_DATA_HOME 后标记对不上导致满跑 3 次重试，已统一为 HOME 推导（packaging/build-deb.sh，与 config.USER_DATA_DIR 逐字一致）。

## 7-3. 热修复记录（v1.0.3，2026-10-14）

**现象**：v1.0.1/v1.0.2 装机后点击图标仍无窗口（用户感知「没法点开」）。

**根因**（真机 `~/.local/share/rime-installer/logs/launcher.log` 实证）：**不是冻结，是 import 期秒崩**——三级重试全部在 1-3 秒内退出码 1，traceback 均为 `ModuleNotFoundError: No module named 'PyQt6.QtSvg'`（qfluentwidgets/common/icon.py:10 急切导入，main.py 模块级导入链早于 main() 的 try/except，故只有 launcher.log 抓到现场）。Ubuntu 的 PyQt6 把 SVG 绑定放在独立包 `python3-pyqt6.qtsvg`，而 deb 的 Depends 只列了 `python3-pyqt6`，用户机装了主包却缺 SVG 分包 → 窗口从未创建。「系统卡死」的最初表现应为反复点击无响应的误判。诚实记录：v1.0.1/v1.0.2 的 Wayland 平台加固经真机证明并非本次根因（属防御性硬化，保留）；三层留痕设计（launcher 重试日志 + 启动标记 + startup-crash）在无法复现场景下锁定了根因。

**修复**：
- `packaging/build-deb.sh`：Depends 在 `python3-pyqt6` 后增加 `python3-pyqt6.qtsvg`（核心修复）。
- `config.py`：`VERSION = "1.0.3"`。
- `smoke_test.py`：版本断言同步 1.0.3；新增 `check_deb_depends()` 防回归——build/ 下有 deb 时用 `dpkg-deb -f <deb> Depends` 断言含 `python3-pyqt6` 与 `python3-pyqt6.qtsvg`，无 deb 时跳过并说明；main() 注册。
- `packaging/build-deb.sh` launcher：三次尝试前加 PyQt6 导入预检，失败时向 launcher.log 写安装提示（`sudo apt install python3-pyqt6 python3-pyqt6.qtsvg`），成功不写。

**验证方式**：`QT_QPA_PLATFORM=offscreen .venv/bin/python smoke_test.py` 期望 SMOKE OK v1.0.3（含 check_deb_depends）；deb 重建后 `dpkg-deb -f <deb> Depends` 含 qtsvg 分包、`dpkg-deb -c` 审计（无 .venv、图标/desktop/launcher 在包内、Version=1.0.3）。

## 7-4. 热修复记录（v1.0.4，2026-10-15）

**现象**（真机 Ubuntu 26.04 + Wayland + Intel Iris Xe，用户实机反馈，附 `launcher.log`）：
1. 软件可打开、按钮可点，但**首页「环境体检」下面显示的信息全是英文**；
2. **鼠标移到软件界面上后一直转圈**（疑似事件循环卡死）；
3. 使用过程中**系统卡住**（Ubuntu 下）。

**证据梳理**（`~/.local/share/rime-installer/logs/launcher.log`）：19:23:11 与 19:31:19 两次启动均为退出码 0 + launch-ok 标记（v1.0.3 的 QtSvg 修复生效）；两次真实会话**没有任何任务日志**（19:20 的批次经内容核对为冒烟测试产物——run_steps 被替换、只有会话头），即转圈/卡死发生在**纯界面浏览**阶段，与 apt/pkexec/ibus 重启等任务路径无关。

**根因与处置**：
1. **英文标签（已定位已修复）**：`ui/pages/welcome_page.py` 把裸 key（`os`/`session`/`desktop`/`frontend`/`engine`/`rime_dir`/`env`）直接传给 `PageBase.add_row()`，左列原样显示成英文标识符；i18n 中预置的 `row.*` 键一直未被欢迎页使用。修复：改为 i18n 键驱动（新增 `row.os`/`row.session`/`row.desktop`/`row.framework`/`row.engine`/`row.env` 与 `welcome.redetect`，中英双包），`retranslate()` 随语言切换刷新标签与按钮，`desktop == "unknown"` 映射为「未知」。
2. **转圈/卡死（无法复现，先仪表化取证）**：offscreen 冒烟全绿；应用层审计无同步阻塞点（`detect()` 各子进程 2s 超时、gsettings 2s 超时、runner 全 QProcess 异步、qframelesswindow 在 Linux 全为空实现、Mica/acrylic 已正确短路）。真机跑 xcb（XWayland）路径且无任何任务执行，怀疑 Qt/xcb 与 XWayland/合成器交互层面。修复（取证优先）：新增 `core/watchdog.py`——GUI 线程 QTimer 每秒心跳，守护线程超 10s 未打卡即判定事件循环卡死，dump 全线程 Python 栈 + 平台决策 + GL 环境变量到 `logs/gui-stall-*.log`；`logs/runtime-trace.log` 记录启动/窗口显示/心跳丢失/恢复/退出面包屑；`main.py` 在窗口显示后接线。若 dump 显示主线程停在 Qt 调用处 → 坐实事件循环阻塞并可直接定位；若 GUI 线程空闲 → 指向合成器/GPU 侧，需换平台策略。
3. **长步骤可观测性**：`core/runner.py` 步骤执行超 60s 仍在运行时周期性记日志（区分「慢」与「挂起」，日志页可见）。

**修复文件**：`ui/pages/welcome_page.py`、`core/i18n.py`、`core/translations/en.py`、`core/watchdog.py`（新增）、`main.py`、`core/runner.py`、`smoke_test.py`、`config.py`（VERSION = 1.0.4）。

**验证方式**：`QT_QPA_PLATFORM=offscreen .venv/bin/python smoke_test.py` 期望 SMOKE OK v1.0.4（新增 `check_watchdog` 断言心跳状态机；窗口验证新增欢迎页行标签中英文切换断言）；offscreen 实跑 6s 验证 runtime-trace 面包屑落盘；deb 重建后 `dpkg-deb -c` 审计（无 .venv、watchdog.py 在包内、Version=1.0.4）。**真机复验待办**：用户升级 v1.0.4 后若仍转圈/卡死，取 `logs/gui-stall-*.log` 与 `logs/runtime-trace.log` 回传即可定位。

**验证方式**：`QT_QPA_PLATFORM=offscreen .venv/bin/python smoke_test.py` 期望 SMOKE OK；offscreen 端到端启动验证；launcher 重试链 stub 测试（秒退触发 3 次尝试、写标记只跑 1 次）；deb 重建后 `dpkg-deb -c` 审计（无 .venv、图标/desktop/platform.py 在包内、Version=1.0.2）。

## 7-5. 热修复记录（v1.0.6，2026-10-16）

**真机问题**：安装引擎与方案后输入法无法使用；环境修复页「部署状态」报无 build 目录，两次「一键修复」后依旧；页面底部出现一团互相重叠的文字。

**根因（日志实证）**：
1. rime 部署是「惰性」的——重启 ibus-daemon 只注册引擎不实例化引擎，只有用户真正切换到「中文(Rime)」输入源时 ibus-engine-rime 才跑部署。旧「重新部署 = 删 build + 重启框架」永不产生 build（20:09/20:11 两次修复日志均 0.3s 完成、build 始终缺失）。
2. GNOME Wayland 会话下 ibus-setup 添加的 preload 不等于桌面输入源——必须在 org.gnome.desktop.input-sources sources 注册 ('ibus','rime') 才能切换到 Rime；旧体检无此检查项。
3. 体检页 `_rebuild_rows` 只删 QWidget item；CheckRow 是 QHBoxLayout（item.widget() 恒 None），旧行 label 从未销毁——每次重新检测后新旧行叠加绘制（底部文字重叠根因）。

**修复**：
1. `core/deploy_guide.build_deploy_script()`：重新部署升级为主动部署——`rime_deployer --build <user_dir> <shared_data_dir> <user_dir>/build` 直接编译生成 build（rime_deployer 随 librime 安装；缺失时退回重启框架），随后重启框架生效。doctor 的 deployed 修复与「重新部署」按钮共用。
2. `core/doctor` 新增「输入源注册」检查项（key=input_source，仅 ibus-rime）：gsettings 读 GNOME sources，无 'rime' → BAD+可修复；修复脚本幂等合并 ('ibus','rime') 进 sources 并重启 ibus。
3. `doctor_page._rebuild_rows` 递归销毁 layout 内子 widget + layout 自身；guide_label 加 wordwrap。冒烟新增双 bind 不泄漏断言（需 sendPostedEvents flush DeferredDelete）。

## 7-5. 热修复记录（v1.0.7，2026-10-17）

**真机反馈**：① 窗口成竖向长条（1080×1030）；② 环境修复页「默认输入法框架」恒显 i「未检测到 ~/.xinputrc」；③ 启动/任务期悬停长时间转圈；④ 方案市场卡片上下错位、皮肤徽标误标「词库增强」；⑤ 皮肤只有 3 款且应用后「没生效」。

**修复**：
1. **窗口尺寸**：PageBase 统一覆写 sizeHint=(1160,780)——滚动区内容曾把页面 sizeHint 撑高、窗口被协商成竖条；主窗口 resize(1180,770) + setMinimumSize(940,640)。
2. **转圈**：detect() 含多个各 2s 超时的子进程，主线程同步跑会被 GNOME 判无响应。新增 `_DetectWorker(QThread)` + `redetect_async()`（完成回主线程分发，`_on_detect_done` 带 manifest 已关闭保护）；欢迎页/体检页/任务结束全部改走 async。runner 高频输出改 120ms 缓冲批量上 UI（LogPage.append_lines）。
3. **xinputrc 项**：框架已在会话中运行时直接判 OK「框架已由桌面会话自动运行，无需 ~/.xinputrc 配置」。
4. **卡片**：SchemeCard 统一 setFixedHeight(330)（瀑布流高度不齐是错位根因）；皮肤分类从 addon 拆为独立 "skin"（徽标「皮肤」+ 筛选「仅皮肤」）。
5. **皮肤**：SKINS 扩充至 6 款（+青竹浅绿/暮山紫深色/暖橙浅色）；皮肤卡 tooltip 说明生效范围——Linux 候选窗由桌面绘制，GNOME Wayland 由 gnome-shell 绘制配色可能不生效，X11 下横竖排/字体等 style 项生效。
6. **冒烟污染真机事故（本轮发现并修复）**：check_skin_flow 只钉住同步 redetect；v1.0.7 任务结束改走 redetect_async 后，后台真实探测覆盖假报告 → 卸载任务的 revert 写到了真实 `~/.config/ibus/rime/default.custom.yaml`（深海皮肤标记块被删）。已修复（`window.redetect_async = _pinned_redetect`）并重新应用深海皮肤恢复真机文件。

## 7-5. 热修复记录（v1.0.8，2026-10-17 · fcitx/Deepin 专项）

**背景**：v1.0.6/1.0.7 的全部真机验证都在 ibus（Ubuntu GNOME）上完成，Deepin 走 fcitx5-rime，需要单独核对 fcitx 路径。

**逐项核对结论**：窗口尺寸 / 检测后台化（转圈）/ 卡片高度 / 皮肤分类 / xinputrc 判 OK —— 全部与前端无关，fcitx 同样受益。两个前端相关缺口已修：

1. **fcitx5 输入法列表注册**（v1.0.6 只补了 GNOME 输入源）：装了 fcitx5-rime 不等于能用，必须把「中州韵」加进 `~/.config/fcitx5/profile` 的 `IMList`。新增检查项 input_source（fcitx5 分支）：含 rime → OK；不含 → BAD 并引导「配置工具 → 添加中州韵(Rime) 并上移」（Deepin：控制中心 → 键盘和语言 → 输入法）。**不做自动改写**——profile 的 IMList 顺序即输入法切换顺序，自动改易打乱用户习惯。旧 fcitx-rime → INFO 引导。
2. **共享数据目录运行时探测**：部署脚本在 rime 共享目录不存在时于脚本内二次探测 4 个候选（/usr/share/rime-data、/usr/share/rime、/usr/share/fcitx5/rime-data、/usr/local/share/rime-data），避免 Deepin 等发行版路径差异导致 rime_deployer 静默失败。
3. **皮肤生效范围文案**：补 fcitx5——fcitx5 使用自有主题（在输入法配置工具里设置），rime 配色在 Linux 各前端下均不生效（X11 仅 style 的横竖排/字体生效；完整配色仅 Windows weasel / macOS squirrel）。

## 7-6. 热修复记录（v1.0.5，2026-10-15）

**现象**（真机 Ubuntu 26.04 + Wayland + ibus-rime，用户实机反馈，附 `runtime-trace.log`、`20261015-202938-engine-install-ibus-rime.log` 与两张截图）：
1. 点「安装引擎」后**提示好几次输入密码**，安装过程中 **Ubuntu 界面非常卡然后卡死**，只有鼠标能动；
2. 鼠标放在软件界面上弹出「状态：引擎已安装，待装方案 ｜ 推荐前端：ibus-rime ｜ …」长条提示；
3. 环境体检「输入法环境变量」一行红色报错：**异常：GTK_IM_MODULE、SDL_IM_MODULE**（实际为 Ubuntu GNOME Wayland + ibus 的标准配置，属误报）；
4. 方案市场卡片区**只露出 2 张卡、下方大片空白**（26 张方案卡的滚动区被压成约半页高）。

**根因与修复**：
1. **多次密码框**：`engine_service.build_install_steps/build_uninstall_steps` 与 `apt_mirror.build_switch_steps` 把 update/install（purge/autoremove、写源/update）拆成多个 Step，每步各包一次 pkexec——polkit 每次提权各弹一次密码框（install 日志实证两次独立 pkexec）。修复：整个任务合并进**单次 pkexec 的 bash -c 脚本**（`&&` 串联 + echo 阶段分隔线），一次密码框走完全部步骤。
2. **安装期系统卡死**：apt 满载（38MB 下载 + 20 包解包）与桌面争抢 CPU/磁盘 I/O。修复：apt 统一经 `nice -n 10 ionice -c 3` 降优先级（`engine_service._deprioritize`/`apt_command`，工具缺失自动降级），安装期间合成器/桌面保持响应。另：单次 pkexec 也消除了叠加的模态密码对话框。安装日志实证任务本身 50s 正常完成（退出码 0），「卡死」为系统级 I/O 调度问题，应用层无阻塞。
3. **悬停弹窗**：`main_window._refresh_status` 给整个窗口挂了 `setToolTip`（状态摘要），悬停任意空白处即弹出。修复：删除窗口级 tooltip，状态由欢迎页摘要与各页状态行承担。
4. **环境变量误报**：`detector.EnvReport.env_vars_ok/env_var_mismatches` 要求四项（GTK/QT/XMODIFIERS/SDL_IM_MODULE）**全部等于**期望值；而 Wayland 会话下 GTK_IM_MODULE/SDL_IM_MODULE 未设定是标准配置（GTK 走 Wayland 文本协议，XWayland/Qt 应用经 XMODIFIERS 的 XIM 回退）。修复：只把**已设定且值与当前前端冲突**的变量判为异常，未设定视为可接受；`doctor.run_checks` 文案同步为「设置冲突」。
5. **方案市场半页高**：`PageBase` 末尾的 `addStretch(1)` 与页面 `insertWidget(count-1, scroll, 1)` 的滚动区按 1:1 平分剩余高度（offscreen 实测滚动区仅 176px/800px、可见 2 卡）。修复：`PageBase.add_expanding_scroll()`——插入滚动区后把末尾 stretch 降为 0，滚动区独享剩余空间（实测 351px、可见 4 卡）；方案市场与体检页同款修复。
6. **体检页导航丢失（分析中发现）**：欢迎页与体检页的标题同为「环境体检」→ objectName（addSubInterface 的 routeKey）相同 → `NavigationPanel.insertItem` 对重复 key 静默 return，体检页导航项被丢弃（侧栏只有 6 项，该页不可达）。修复：ZH `nav.doctor`/`doctor.title` 改为「环境修复」（EN 维持 Diagnostics，本就区分）；`PageBase` 与 i18n 增加 routeKey 唯一性警告注释；冒烟断言收紧为 `items == 7` 且 objectName 全唯一。

**修复文件**：`core/engine_service.py`、`core/apt_mirror.py`、`ui/main_window.py`、`core/detector.py`、`core/doctor.py`、`ui/pages/base.py`、`ui/pages/scheme_page.py`、`ui/pages/doctor_page.py`、`core/i18n.py`、`smoke_test.py`、`config.py`（VERSION = 1.0.5）、`packaging/build-deb.sh`。

**构建事故修复（同轮）**：`build-deb.sh` 原用固定 `build/deb` 暂挂 + `rm -rf` 清场；宿主 safe-delete 钩子对批量删除按轮次累计计数并拦截，`rm` 被拦截时（`2>/dev/null || true` 静默吞掉）旧暂挂树残留，`tar -xf` 将新旧内容**合并**——实测把上一版 `.smoke-tmp` 的 54 个条目打进 v1.0.5 deb。修复：暂挂目录改 `mktemp -d` 全新目录（版本无关、无需任何删除），tar 排除行补 `./.smoke-tmp` 与 `./tools/diag_layout.py`。

**验证方式**：`QT_QPA_PLATFORM=offscreen .venv/bin/python smoke_test.py` 期望 SMOKE OK v1.0.5（安装/卸载/镜像切换均断言「单步单次 pkexec + nice 降级」；环境变量三项语义断言——Wayland 标准配置通过、GTK=fcitx 冲突检出、无框架时为 None；导航 7 项严格相等 + objectName 唯一 + 滚动区高度过半断言；窗口无 tooltip 断言）；shell 引用 round-trip + `bash -n` 语法验证；deb 重建后 `dpkg-deb -c` 审计（smoke-tmp/diag/.venv/Trash 均为 0、Version=1.0.5、Depends 含 qtsvg）。**真机复验待办**：升级 v1.0.5 后验证安装引擎仅一次密码框、安装期桌面响应、悬停无弹窗、环境变量行变绿、方案市场卡片区通铺。

---

## v1.0.11 — Deepin 25 方案市场安装失败修复（只读根文件系统）

**现象**：deb 安装到 Deepin 25 后，方案市场安装任何方案均失败；日志在「解压」步报 `mkdir: 无法创建目录 "/usr/lib/rime-installer/resources/plum/package": 只读文件系统`（退出码 1）。下载本身成功（落盘 `~/.local/share/rime-installer/cache/...`），故表现为「下载成功、解压失败」。

**根因**：加速路径把下载的方案 tarball 解压预置到 plum 自带的 `package/` 目录，而 `PLUM_DIR` 原先直接等于 `RESOURCES_DIR / "plum"`，安装态下 `PROJECT_ROOT = /usr/lib/rime-installer`，该目录在 Deepin 25 不可变根下为只读。只要 `PLUM_DIR` 指向只读安装树，`package/` 必只读——结构性死结，与具体方案无关。日志/快照/清单/引擎清理此前已全部落用户家目录，唯独 plum 运行时缓存依赖安装树可写。

**修复**：`config._resolve_plum_dir()` 解析可写 plum 目录——
- bundled 可写（dev/源码运行）→ 直接用，行为不变；
- 只读安装树 → 把 `resources/plum` 同步到用户目录 `~/.local/share/rime-installer/plum`（仅缺失或 bundled 更新时整树复制，忽略 `package/` 与 `.rw-probe`），`PLUM_DIR` 指向副本。
rime-install 用 `$0` 推导 `root_dir`，副本里的 `package/` 自然可写；解压/清缓存/rime-install 三处一致走可写副本，**零 plum 改造**。`_plum_dir_writable` 用实测写入探针识别只读挂载（`os.W_OK` 仅看权限位不可靠）。

v1.0.11 同批硬化（Deepin 部署专项核查发现，均非阻断项）：
- `core/detector.py` `_recommend_frontend` 桌面匹配补 `"DDE"`：Deepin 25（及同系 UOS V25）上报的 `XDG_CURRENT_DESKTOP` 实测为 `DDE`，不含 `DEEPIN` 子串。正常会话会被 `active_frontend`（fcitx5 常驻进程）旁路，但无前端进程运行的边缘场景（极简会话/开机早期/pgrep 超时）会误推 `ibus-rime`，使 fcitx5 集成（v1.0.8 的 profile IMList 注册）落空。
- `config._resolve_plum_dir` 对 `_sync_plum_to`（`copytree`）加 `try/except OSError` 回退：home 不可写/磁盘满/权限异常时回退 bundled 目录并 stderr 留痕，保证 `import config` 不崩（应用可启动，写入错误改在安装步骤中暴露），而非启动即崩。

**修复文件**：`config.py`（VERSION = 1.0.11，`_resolve_plum_dir`/`_sync_plum_to`/`_plum_dir_writable`）、`core/detector.py`（DDE 匹配）、`smoke_test.py`（版本断言 1.0.11；`check_detector` 新增 `_recommend_frontend` 桌面矩阵断言：DDE/Deepin/KDE→fcitx5-rime，GNOME/unknown→ibus-rime）。

**验证方式**：`config` 导入后 dev 模式 `PLUM_DIR == resources/plum`（可写）；模拟只读（`_plum_dir_writable→False`）时 `PLUM_DIR == ~/.local/share/rime-installer/plum`，副本含 `rime-install`(0o775) 与 `scripts/`，不含残留 `package/`；模拟 `_sync_plum_to` 抛 `OSError` 时回退 bundled 且 stderr 留痕（不崩）。`_recommend_frontend` 桌面矩阵实测 5 例全过（DDE/Deepin/KDE→fcitx5-rime；GNOME/unknown→ibus-rime），`active_frontend` 旁路优先不变。deb 重建后 `dpkg-deb -c` Version=1.0.11。**真机复验待办**：Deepin 25 重装 deb 后 ① 方案市场可正常下载+解压+安装 rime-ice 等方案；② 前端推荐显示 `fcitx5-rime` 且 `pkexec apt-get install fcitx5-rime` 引擎安装走通（磐石只读根下标准 apt 已适配，需设备上验一次）。

---

## v1.0.15 — fcitx5 输入法列表误报修复（真机实证）

**现象**（真机 Deepin 25 + fcitx5-rime，用户实机反馈）：输入法实际可用（fcitx5 输入法菜单有「中州韵」，`DefaultIM=rime`），环境修复页却红字提示「fcitx5 输入法列表未包含「中州韵(Rime)」（当前：空）——不添加则无法切换到 Rime 输入」，恒显示「1 个问题待处理」。

**根因**：`doctor._fcitx5_input_methods()` 只正则匹配 `^IMList=` 行，而 fcitx5 的 `~/.config/fcitx5/profile` 是 ini 风格分段结构——输入法注册在 `[Groups/<组>/Items/<序>]` 段的 `Name=`（fcitx5-rime 注册名为 `rime`），根本没有 `IMList=` 键。文件存在但匹配不到 → 返回空串 → 误判 BAD。组名段 `[Groups/0]` 同样带 `Name=`（如「默认」），解析时必须按段名区分，不能见 `Name=` 就收。

**修复**：`core/doctor.py` 重写解析——遍历分段，仅收 `[Groups/*/Items/*]` 段的 `Name=`（去重保序，逗号连接），兼容个别配置的 `IMList=` 行；函数加可选 `profile` 参数（与 `user_data_dirs(frontend, home)` 同惯例）便于冒烟直测。

**验证**：`smoke_test.check_doctor()` 新增真实格式 profile 直测（解析出 rime、组名不误收、IMList 兼容、文件缺失返回 None、无输入法段返回空串）+ fcitx5 三态注入断言（已注册 ok / 未注册 bad 且详情列出当前输入法 / 无配置 info），不再依赖真机 profile 状态；真机 home 实跑 `run_checks` 输入法列表项变 ok。版本 bump v1.0.15。

---

## v1.0.16 — 外观页重构：砍主题市场，保留切换 + 打开目录（用户决策）

**决策背景**（用户实机反馈）：外观页「主题市场」下载链路（aria2 多镜像 codeload → 解压 → 勾选对话框 → 安装 → manifest 记账 → 卸载）过重，且「切换不生效」根因是 `appearance_apply.py` 只改 `classicui.conf` 不重启 fcitx5。用户选择方案 A：砍市场、保留本机主题枚举+一键切换（补重启）、新增「打开主题目录」按钮+教程文案。

**修复点**：
1. **切换即时生效**：`core/appearance.py` 的 `build_theme_steps` / `build_theme_revert_steps` 各追加 `_restart_fcitx5_step`（pkill fcitx5 + setsid fcitx5 -d，与 deploy_guide 同款）。classicui 主题进程启动时加载，只改配置不重启 → 候选窗无变化（用户「无法切换」根因）。`RIME_INSTALLER_NO_RESTART=1` 时跳过重启（冒烟专用）。
2. **深浅标签失真修复**：图片型主题（deepin 系，`[InputPanel/Background]` 带 `Image=`，`Color=#ffffff` 仅为兜底色）解析为 `palette=None/dark=None`，UI 中性占位、不标深浅；此前按兜底色判深浅会把 deepin-dark 标成「浅色」（真机实证）。
3. **状态行去重**：`deepin-dark (deepin-dark)` key/name 重复展示修掉（name==key 时只显示其一）。
4. **砍市场**：删除 `core/theme_market.py`、`tools/theme_market_apply.py`、`ui/theme_picker_dialog.py`、`config.THEME_KEY_PREFIX` 常量引用；外观页删 `_MarketSourceRow`/市场分区/市场信号/`set_market_theme_keys`/`confirm_theme_uninstall`；主窗口摘市场任务链（下载/勾选/安装/卸载 + InfoBar 分支 + `theme:` 过滤 + `ThemePickerDialog` 导入）；state_machine 删 `theme_market_*` 三条规则；i18n 删除 `appearance.market_*`(21) + `msg.theme_market_*`(7)，新增 `appearance.open_themes_dir`/`open_themes_dir_tip`/`tone_dark`/`tone_light`，`theme_note` 改为教程文案（目录位置 + 切换自动重启 + 打开目录放自制主题）。
5. **冒烟适配**：删 3 个市场专项（check_theme_market/ui/flow，共 536 行）+ main 调用 + rerun_flow_checks 映射；`check_appearance` 新增图片型主题不标深浅 + 切换/还原均含重启 step 断言；`check_appearance_ui` 改 `tone_dark/light` 断言 + 补「打开主题目录」按钮断言；main 设 `RIME_INSTALLER_NO_RESTART=1` 防误杀真机 fcitx5；版本断言 1.0.16。

**验证**：完整冒烟 SMOKE OK（v1.0.16）。

**交付形态**：外观页 fcitx5 分区 = 主题行（迷你预览 + 深浅/无配色占位 + 当前徽标 + 切换）+「恢复系统默认」+「打开主题目录」；ibus 分区保留。换自制主题路径收敛为「打开目录放文件夹 → 回本页点切换」一条路。

---

## v1.0.17 — 主题查找补齐系统目录（真机实证）

**现象**（真机 Deepin 25）：切到 `default` 报「未知主题」。根因：枚举用的系统目录与 fcitx5 实际搜索路径不一致——fcitx5 搜索主题时**系统目录（`/usr/share/fcitx5/themes`）与用户目录都查**（用户优先），而 CLI 校验只查用户目录；`default` / `default-dark` 编译内置或位于系统目录，用户目录里根本没有。

**修复**：`core/appearance.py` 的 `theme_exists()` 改为「classicui 编译内置（`default` / `default-dark` 放行）/ 内置完整主题 / 用户目录 / 系统目录」四处任一命中即通过。

**验证**：`check_appearance` 新增「自定义目录下无 default 仍应通过校验」断言；真机切 `default` 成功。

---

## v1.0.18 — 内置完整主题：随包发布 Mellow 系列

**背景**：用户希望开箱就有好看的主题，而不是自己找仓库下载。

**修复点**：
1. **整包内置**：`resources/bundled_themes/<key>/`（theme.conf + svg）随 deb 发布，`appearance.list_bundled_full_themes()` 枚举并**无条件前置**进列表（`source=installer`），无需先安装即可见可切；磁盘扫描跳过这些 key 防重复。`appearance_apply` 切到该 key 时整包复制到用户主题目录再写 `classicui.conf`。上游 zip（`fcitx5-mellow-themes-main.zip`）已排除出包。
2. **预览有色彩**：内置主题走 `_bundled_full_theme_info()` 强行解析五色（图片型主题也取 `panel.svg` 填充色作底），不再 `palette=None` 中性占位。
3. **上游主题兼容性修复**（真机实证）：
   - 上游 zip 每款主题引用 `radio.svg`（Menu/CheckBox）与 `arrow.svg`（Menu/SubMenu），但仓库根本不提供这两个文件 → 缺图。已为 10 款主题各生成对应 svg（描边取各自 `InputPanel NormalColor`，保证在面板上可见）。
   - 另 5 款 `-dark` 主题 `[Menu] NormalColor=#000000` 落在深色 `#151515` 面板上，菜单文字不可见 → 修正为等于各自 `InputPanel NormalColor`（浅色）。
   - 两类修复均落在 `resources/bundled_themes/` 的副本，安装副本同步保留。
4. **刷新按钮**：外观页加「刷新主题目录」（`_refresh()` 重跑 `_fill()`），用户把主题文件夹丢进 `~/.local/share/fcitx5/themes` 后点刷新即见，无需重启。
5. **冒烟**：`check_deb_depends()` 固化；`check_appearance` 断言内置主题存在性校验与上游仓库地址常量。

---

## v1.0.19 — 外观页展示重做：卡片网格 + 双标签页 + 上游署名

**用户反馈**：皮肤主题已能正常切换，但①截图框选了 4 个系统主题（`default` / `default-dark` / `deepin-*`）要求移除；②现有缩略图与展示方式不好看，需重新设计；③建议内置主题与用户自定义主题分两个标签页；④为内置主题来源写开源贡献感谢。

**修复点**：
1. **移除系统自带主题**：`core/appearance.py` 新增 `split_themes()`，按 `source` 拆成「内置（installer）/ 我的（user）」两份，系统自带（`system`）不进界面——它们与「挑一套好看的候选窗」无关，只是噪声；要回出厂态有「恢复系统默认」，功能无损失。若当前主题恰是系统自带（不在卡片里），显示一行提示而不是让列表「空着不说话」。
2. **候选窗预览重做**：`ui/theme_swatch.py` 的 `CandidatePreview` 从「五色色块」改为**用主题自带的 svg 画一个真实候选窗**——`panel.svg` 九宫格铺底 + `highlight.svg` 作高亮候选条 + 序号/候选词按 `InputPanel` 真实色渲染。切片边界取比例（`10/31`、`14/31`）而非固定像素，避免整体拉伸把圆角拉成椭圆。svg 光栅缓存（path+mtime+尺寸 → pixmap）避免十张卡反复重绘重复解析。
3. **卡片网格**：`_ThemeCard`（候选窗预览 + 名称 + 深浅标签 + key + 切换按钮 / 当前态徽标，整卡可点）+ `_CardGrid`（按可用宽度自动 2~4 列，窗口拉宽自动铺满）。当前态用强调色描边 + 徽标并**禁用并隐藏**切换按钮（v1.0.19 冒烟修正：`_emit_switch` 已守卫 `not self._current`，按钮再禁一次是纵深防御）。
4. **双标签页**：`SegmentedWidget` 切「内置主题（10）/ 我的主题（2）」，标签带数量；「我的主题」为空时显示虚线框空状态 + 一句可操作提示 + 主题目录路径脚注。
5. **上游署名**：`BUNDLED_THEMES_REPO` / `BUNDLED_THEMES_AUTHOR` / `BUNDLED_THEMES_LICENSE` 常量 + 页内鸣谢卡（心形图标 + 说明 + 仓库链接 + 「查看开源项目」按钮走 `link_requested` → `QDesktopServices`）。BSD-2-Clause 许可证原文随主题目录分发（`resources/bundled_themes/LICENSE`）。
6. **i18n**：新增 `appearance.tab_builtin` / `tab_custom` / `tab_label` / `card_current` / `theme_switch` / `theme_switch_tip` / `custom_empty_title` / `custom_empty_hint` / `custom_hint` / `thanks_title` / `thanks_body` / `thanks_note` / `thanks_open` / `system_theme_note`，中英双语补齐（`system_theme_note` 占位符为 `{name}` 而非 `{key}`——与 `i18n.tr` 的 `key` 位置参数同名会导致格式化冲突）。
7. **冒烟**：`check_appearance_ui` 全面重写——卡片高度/预览宽度、当前态徽标与按钮可用性、Mellow 进内置标签页、`pearl` 进我的标签页、系统主题不进界面、标签切换内容互换、鸣谢链接信号、空目录空状态；版本断言 1.0.19。

**验证**：完整冒烟 + `tools/rerun_flow_checks.py` 分批补跑全部 OK；离屏渲染明/暗两套主题 × 内置/我的两页共 8 张截图逐张核对。

---

## v1.0.20 — 自定义方案：跟随仓库默认分支 + 拒绝「非 RIME 包」的假成功

**用户反馈**：在方案市场粘贴 `https://github.com/thep0y/fcitx5-themes-candlelight` 点「安装自定义方案」，三步下载全部失败（日志里 codeload 直连 404、gh-proxy 404、ghfast 403）。

**根因（两层）**：
1. **分支硬编码 master**：`parse_spec()` 未写 `@branch` 时默认 `master`，而该仓库默认分支是 `main`（GitHub API 实测 `default_branch=main`，`refs/heads/master` → 404、`refs/heads/main` → 200、`tar.gz/HEAD` → 200）。内置的 rime-ice 早在 v1.0.13 就显式写了 `branch="main"`，但**自定义入口没享受到这次修正**。
2. **该仓库不是 RIME 方案包**：它是 fcitx5 皮肤合集（根目录只有 README / install.sh / `autumn` `green` `spring` `summer` `winter` `transparent-green` `macOS-dark|light`，**没有任何 `*.yaml` / `recipe.yaml`**），`install.sh` 的目标是 `~/.local/share/fcitx5/themes`。plum 的 `install-packages.sh` 无 recipe 时只收集根目录 `*.yaml/*.txt/*.gram` + `opencc/*`，收不到文件就打印 `No files updated.` 并 **exit 0** —— 即便分支修好，也会「安装成功」但 RIME 里什么都没多。

**修复点**：
1. **默认分支探测**（`core/schemes.py` + `core/scheme_service.py`）：`PackageRef.branch` 默认值改为 `""` 表示「跟随仓库默认分支」，`spec` / `label` 不再输出悬空 `@`；`tarball_urls()` 首选 `.../tar.gz/HEAD`（由 GitHub 解析默认分支）走「直连 + gh-proxy + ghfast」三线路，再以 `refs/heads/main`、`refs/heads/master` 各一条直连兜底（共 5 条，避免一次失败刷 9 条日志淹没真错误）。显式 `@branch` 行为不变，仍保留三线路镜像。
2. **包内容校验**（`build_verify_step`）：解压后、`rime-install` 前检查 `plum/package/<pkg_dir>` 根目录是否存在 `*.yaml` / `*.txt` / `*.gram` 或 `opencc/` 子目录；没有则该步失败并**中止**，不进入 rime-install——把「假成功」变成明确错误。
3. **失败提示可读化**：`Step` 新增 `hint_key`（约定同名 `_hint` 为正文），`TaskRunner.failed_step` 记录出错步骤，`_on_task_finished` 据此弹定向 InfoBar：标题「这个地址不是 RIME 方案包」+ 正文说明「没找到任何 RIME 配置，已停止安装，你的配置没有被改动」并给出两条去向——皮肤/主题 →「外观」页放主题文件夹后点「刷新主题目录」；输入方案 → 确认仓库根目录直接有 `default.yaml` 之类文件。
4. **i18n**：新增 `msg.not_rime_package` / `msg.not_rime_package_hint`（中英双语）。
5. **冒烟**：`check_schemes` 断言未指定分支时 `branch` 留空且 `spec/label` 无悬空 `@`、`is_auto_branch`；`check_scheme_service` 断言默认 HEAD 优先 + 三条 HEAD 线路 + main/master 兜底 + 显式分支仍 3 条线路 + 校验步骤存在且带提示键，步骤数随之 +1；`check_scheme_install_flow` 加跑校验步骤，并新增反向用例（只含 `theme.conf` + `panel.svg` 的「主题包」必须被拦下且输出含「RIME 数据文件」）；版本断言 1.0.20。

**用户侧即时可用方案（无需改代码）**：想要的烛光皮肤属于「外观」页——下载 zip 解压后把 `autumn/` 之类的主题目录复制到 `~/.local/share/fcitx5/themes/`，在「外观」页点「刷新主题目录」再切换；若确有别的方案仓库默认分支为 `main`，旧版可直接写 `user/repo@main` 绕过。

**验证**：完整冒烟（schemes / scheme_service / i18n / window / 各流程项）+ `tools/rerun_flow_checks.py` 分批补跑全部 OK；清理 `resources/plum/package/smokelab` 与 `.smoke-tmp` 测试残留，避免混入 deb。

---

## v1.0.21 — 外观页支持「从链接导入 fcitx5 主题」

**用户反馈**：`https://github.com/thep0y/fcitx5-themes-candlelight` 这类主题仓库很多，贴一个网址后能否直接导入其中的主题？

**背景**：v1.0.14 做过「主题市场」，v1.0.16 因「下载→勾选→安装→卸载 链路过重」整块移除；被移除的是**固定源目录**，底层的「aria2 下载 → 解压 → 枚举 theme.conf → 拷进用户主题目录」能力仍然适用。本次改由**用户粘贴的链接**驱动，并刻意保持极简。

**决策（用户确认）**：
1. **一次全导**：一个仓库里的主题全部导入，不弹勾选框（避免重演 v1.0.14 的多步链路）；
2. **双入口**：外观页「我的主题」页签内输入框 + 方案市场「不是 RIME 方案包」提示里的「去外观页导入主题」跨页联动。

**实现**：
1. **`core/theme_import.py`（新增）**：`enumerate_themes()` 支持两种上游形态——目录模式（顶层每个含 `theme.conf` 的子目录为一款，烛光 10 款 / zhiim 14 款）与变体模式（根级 `theme-*.conf` + 根级图片资产，hosxy Material）；`valid_theme_name()` 拒绝 `..`／绝对路径／分隔符／前置横线；`install_theme()` **整目录拷贝**（图片型主题的 `Image=panel.png` 是相对引用，只拷 conf 会缺图），变体模式把 `theme-<x>.conf` 落成 `theme.conf` 并只带根级图片；重名先清后拷；`install_all()` 单款失败不中断并返回失败说明。
2. **`ui/pages/appearance_page.py`**：`custom_host` 顶部新增 `LineEdit` + `导入主题` 按钮，`parse_spec` 校验格式，输入为空/非法/状态机不允许/执行中一律禁用；新增 `theme_import_requested(str)` 信号、`prefill_import()`（跨页带链接并切到「我的主题」）、`refresh_themes()`、`clear_import_input()`；`_apply_rules`/`set_busy`/`retranslate` 全部接入。
3. **`ui/main_window.py`**：`_on_theme_import()` 走 TaskRunner 两步（`build_download_step` + `build_extract_step`，复用 v1.0.20 的 HEAD 默认分支探测与多镜像）；任务成功后 `_commit_theme_import()` 枚举 → 全量安装 → 刷列表 + InfoBar 汇总；仓库里没有 `theme.conf` 时提示「这个仓库里没有 fcitx5 主题」，并引导去「方案市场」。任务类型 `appearance-import`（`_target_page` 按 `appearance` 前缀路由到外观页）。
4. **跨页联动**：`msg.not_rime_package` 那条失败提示用 `InfoBar.addWidget()` 挂一颗「去外观页导入主题」按钮，点击 `_goto_theme_import()` → `prefill_import(url)` → `switchTo(appearance_page)`，省掉「看完提示自己找入口再粘一次」。
5. **`config.py`**：`THEME_MARKET_CACHE` → `THEME_IMPORT_CACHE`（`cache/theme-import`），`ensure_user_dirs` 同步。
6. **i18n**：新增 `appearance.import_placeholder` / `import_button` / `import_tip` / `import_tip_invalid` / `import_note`、`msg.theme_imported(_hint)` / `msg.theme_import_empty(_hint)` / `msg.go_import_theme`，中英双语。
7. **冒烟**：新增 `check_theme_import`（两种形态枚举、显示名回退、非法目录名拦截、整目录拷贝含资产、重名清旧、变体落成 theme.conf 且不夹带其它变体、批量单款失败不中断、越界名不落盘、空仓库枚举为空）；`check_appearance_ui` 补导入控件可用性/prefill 联动/busy 禁用；`tools/rerun_flow_checks.py` 收录 `theme_import`（零删除模式），并新增 `_PRISTINE_RMTREE` 哨兵——补跑脚本把 `shutil.rmtree` 置空时跳过「旧资产已清」这一条断言，两种模式都能跑。

**刻意不做**：主题市场目录、多源列表、勾选框、manifest 记账与卸载（要删主题走「打开主题目录」）。导入 ≠ 生效：仍须点卡片切换（改 `classicui.conf` + 重启 fcitx5）。

**验证**：
- 真实仓库端到端：`thep0y/fcitx5-themes-candlelight` 枚举 10 款、安装 10/10 成功，逐款核对资产（autumn/green 等 4 文件、macOS-* 含 svg/png 变体）。
- 跨页联动脚本实测：失败提示出现 1 条 InfoBar + 「去外观页导入主题」按钮，点击后 `stackedWidget` 切到「外观」，输入框预填原链接。
- 完整冒烟 + `tools/rerun_flow_checks.py` 五批补跑全绿；真机 `~/.local/share/fcitx5/rime/default.custom.yaml` 与用户主题目录未被污染。

---

## v1.0.22 — 主题导入兼容「绝大部分主题仓库」（实测 27 个高星仓库）

**用户诉求**：v1.0.21 只验证了 `thep0y/fcitx5-themes-candlelight` 一种形态，需要确认还有哪些 fcitx5 主题项目能导入，并补齐兼容方案。

**调研方法**：GitHub Search API 取 `fcitx5 theme` 按 star 排序的前 27 个仓库，全部用 `codeload .../tar.gz/HEAD` 下载、解压、逐仓库统计 `theme.conf` 位置与根目录结构，再用真实文件跑枚举+安装回放（不是看 README 猜）。

**实测出的 5 种上游形态**：

| 形态 | 代表仓库（款数） | v1.0.21 | v1.0.22 |
|---|---|---|---|
| L1 根目录单主题（theme.conf 在仓库根） | drbbr/dracula、iovxw、Vrchz/Psionics-Remix、escape0707/adwaita-dark、skykeyjoker/Bing、weearc | ✗ 0/6 | ✓ |
| L2 顶层多目录（每目录一主题） | candlelight 10、Fluent 4、sogou 12、mellow 10、inflex 10、nord 2、Ori 2、mint 4、macos12 2、wechat 2、ayaya 2、pt-cute 4、Rexoen 2、deepinlike 2、rose-pine 3 | ✓ | ✓ |
| L3 嵌套子目录（`src/<变体>/`） | catppuccin/fcitx5 **56 款** | ✗ 0/56 | ✓ |
| 变体模式（根级 `theme-*.conf`） | hosxy/Material-Color 10、pu-007/gruvbox 6 | ⚠ 能读但**全部同名互相覆盖**，10 款只剩 1 款 | ✓ 撞名才补后缀 |
| 非桌面主题 | tankb52（Android JSON）、fcitx-contrib（android/macos 配色）、scratch-er/breeze（build.py 源码） | ✗ 静默 0 款 | 明确诊断原因 |

**结论**：27 个仓库里 24 个可直接导入（共 150 款），3 个非桌面主题仓库给出确定结论。v1.0.21 的有效覆盖率约 49%（73/150），v1.0.22 为 100%。

**实现**：
1. **分层枚举**（`core/theme_import.py`）：`_theme_dirs_at_layer(root, 1|2|3)` 依次看「仓库根 → root/*/ → root/*/*/」，**取最浅的非空层**。分层而非全量递归，避免把 `example/`、`template/` 里的示例配置误当主题（catppuccin 的 `templates/*.tera` 就是干扰项）。
2. **L1 根目录主题**：`ImportTheme.root_theme=True`，key 取 `[Metadata] Name` 并做文件名清洗（空格/分隔符→`-`，保留中文），Name 缺失回退仓库目录名。
3. **L1+L4 共存**：根目录有 `theme.conf` 时同时枚举同层 `theme-*.conf`（gruvbox 形态：默认款 + 6 个配色变体）。
4. **变体 key 唯一化**：先按 Name 定 key，**只有撞名的那几款**才补变体后缀 —— hosxy/gruvbox 的 10 个变体 `[Metadata] Name` 全叫同一个名字，v1.0.21 会依次覆盖只剩最后一款（真实缺陷）；Name 本就互不相同的不加后缀（避免把 `Material-Red` 变成 `Material-Red-red`）。
5. **资产按引用挑，不整根目录搬**：新增 `_image_refs()` 解析 theme.conf 的 `Image=` / `OverlayImage=`（允许行首缩进，跳过注释与绝对路径），根目录/变体模式只拷被引用的图片；一个都没解析到才回退「全部根级图片」。实测 skykeyjoker 根目录 51 张 png（含 50 张 highlight 预设 + .psd，2.4MB），精确取用只需 4 张。设计源文件（.psd/.xcf/.ai/.sketch/.kra）永不拷入。
6. **空仓库诊断**（`diagnose_repo()`）：①JSON 含 `isDark`+`keyBackgroundColor` → 手机端主题；②`*.conf` 含 `[LightMode]/[DarkMode]`+`OverrideDefault` → android/macos 配色方案；③`build.py` / `config/*.conf.in` / `*.tera` → 需自行构建。`_commit_theme_import()` 据此弹定向提示，判不出来才用通用话术；同时修掉「空仓库分支直接 return 不清理解压目录」的缓存泄漏。
7. **i18n**：新增 `msg.theme_android` / `msg.theme_color_scheme` / `msg.theme_source_only` 及各 `_hint`（中英双语）。
8. **冒烟**：`check_theme_import` 扩到五种形态 —— L1 根目录主题（含「只拷被引用图、不搬 README/.psd/截图」）、L3 嵌套（`src/<变体>` 的 rel_dir）、同名变体补后缀（3 个同名 → 3 个唯一 key）、根目录主题与变体共存（`gruvbox` + `gruvbox-aqua`）、变体 Name 不同时不加后缀（`Blue` / `Material-Red`），以及三条诊断分支的正反用例（普通仓库不得被误诊）。

**刻意不做**：不做 fcitx5-android JSON → 桌面 theme.conf 的自动转换（两套控件的配色语义不同，硬转会得到错误观感）；不处理只在 Releases 里发布成品的仓库（如 scratch-er/breeze），只提示「去 Releases 下载」。

**验证**：
- 27 个真实仓库全量回放：134 个 `theme.conf` + 16 个变体 conf = **150 款全部落盘，0 失败**；逐款核对「theme.conf 里的每个 `Image=` 都能在落地目录里找到」，仅上游本身就缺的（mellow 的 radio.svg/arrow.svg、skykeyjoker 的 highlight.png）不计。
- 界面耗时：56 款用户主题 + 10 款内置 = 66 张卡片，`_fill()` **0.76s**（SVG 光栅有缓存），无需懒加载。
- 四类空仓库走 `_commit_theme_import()` 实测文案正确、解压目录均已清理。
- 完整冒烟 + `tools/rerun_flow_checks.py` 四批补跑全绿。

---

## v1.0.23 — 备份恢复页全量加固（闪退 / 启动失败 / 恢复清空配置）

**用户诉求**：备份恢复页「点打开备份目录软件就闪退」，要求全面分析验证该页所有功能并给出修复方案。

**根因（均实证复现）**：

| # | 缺陷 | 位置 | 证据 |
|---|---|---|---|
| P0-1 | 打开备份目录闪退 | `ui/pages/backup_page.py:271` | `QDesktopServices.openUrl(Path.as_uri())` 传的是 `str`，PyQt6 只收 `QUrl` → `TypeError` → 槽函数异常逃逸 → 进程 `SIGABRT(134)`（PyQt6 对槽内未捕获异常直接 abort，无提示无日志） |
| P0-2 | 备份目录里有一个畸形 `backup.json`，**整个软件启动失败**；导入畸形包闪退 | `core/snapshot_service.py:180-181`、`ui/main_window.py:181,930` | `int("abc")` / `packages=3` 抛 ValueError/TypeError；`MainWindow.__init__` 的 `list_backups()` 直接抛 → 启动失败弹框；导入路径只捕 `ValueError`，TypeError 逃逸 → 134 |
| P0-3 | 恢复中「取消任务」= 配置目录被清空 | `core/snapshot_service.py:234-257` | clean 模式「先删全部文件、再逐个写」；在 2% 进度强杀后目标目录文件数 801 → 0（未加固前） |
| P0-4 | 空目录备份「假成功」→ 可用「清空后恢复」清空用户配置 | `snapshot_service.py:101-142,186-191` | 生成 0 文件 zip 且 `valid=True`，恢复统计 `removed=1, extracted=0` |

**修复**：
1. **打开目录**（`backup_page.py`）：改 `QUrl.fromLocalFile(str(dir))`；`mkdir`/`openUrl` 全部 try/except，创建失败与「系统无文件管理器」都转成 InfoBar 提示而不抛异常。
2. **元数据防御**（`snapshot_service.py`）：新增 `_as_int/_as_text/_as_str_list`；`read_backup_info` 全路径 try/except，任何异常都返回 `valid=False`；`list_backups` 逐项隔离 + 改按 `mtime` 排序（此前按文件名，导入的任意名 zip 会冒充「最近备份」）。
3. **导入先校验**：`import_backup` 复制前 `read_backup_info` 校验，`OSError` 统一转 `BackupError(ValueError)`；`_on_backup_import` 补捕 `(ValueError, OSError)` 与兜底 `Exception`（槽函数内不允许异常逃逸）；导入成功后自动选中新项。
4. **拒绝假备份**：`create_backup` 对「目录不存在 / 0 个文件」抛 `EmptyBackupError`；CLI 返回 2；`StateMachine.rule("backup")` 增加 `is_dir()` 要求；页面「配置目录」行在目录缺失时显示「（尚未创建）」警示色。
5. **两阶段恢复**（`restore_backup`）：先解压到同级 `.rime-restoring-<name>-<pid>/`，**再把文件搬进目标目录，最后才清理备份之外的多余文件**。顺序反过来（先删后写）一遇强杀就是空目录；按此顺序最坏只残留旧文件。0 文件/全为越界成员的备份直接拒绝；开跑前回收上一次被强杀留下的临时目录。
6. **恢复不可取消**：`BackupPage.set_busy(..., cancellable=False)`，`_start_task` 对 `backup-restore` 传 False，按钮 tooltip 说明原因。
7. **按钮态单一事实源**（`backup_page.py`）：`set_backups` 重建时 `blockSignals`，结束后统一 `_refresh_actions()`；删除判据改 `selected_info()`（此前用 `selected_path()`，在列表重置中途取到已删项 → 删除按钮停在可用）；恢复按钮同时参考 `sm.rule("restore_backup")`（无配置目录时禁用并给原因，此前可点且点击后静默无反应）。
8. **恢复目标可见**（`confirm_restore`）：新增「将还原到」，目标不存在 / 与备份来源不一致时追加警示；`parent=None` 时回退 `QApplication.activeWindow()`（此前直接 `AttributeError`）。
9. **恢复后闭环**：成功 InfoBar 挂「重启输入法框架」按钮（复用体检页 `restart_requested`）；`_on_backup_restore` 对 `rime_dir is None` 弹提示；目标目录为空/不存在时跳过预备份（否则 `EmptyBackupError` 会中断整个恢复）。
10. **命名冲突**：`new_backup_path` 同秒同名自动顺延 `-2/-3`（此前静默覆盖前一个备份）。
11. **i18n**：列表行、数量/最近备份值、确认框、删除框、全部提示改为 i18n 键（新增 `backup.row_*`、`backup.confirm_*`、`msg.backup_failed`、`msg.restore_failed`、`msg.open_dir_*` 等中英双语）；`retranslate()` 重建列表；导出默认目录遵循 `XDG_DOWNLOAD_DIR`。
12. **布局**：取消/查看日志从列表下方移到进度区右侧（截图里 7+2 个按钮挤成两行）；`delete_backup` 默认只允许删除备份目录内的 `.zip`（与提示文案一致，测试可用 `allowed_dir` 指定白名单）。
13. **失败提示**：`build_backup_step` / `build_restore_step` 加 `hint_key`（`msg.backup_failed` / `msg.restore_failed`），失败给定向建议而非退出码。

**冒烟新增断言**（`check_snapshot_service` / `check_window`）：4 类畸形元数据不抛且坏项标 invalid、坏项不阻塞整表；空目录拒绝备份且不留 zip；0 文件备份的清空恢复被拒且现有配置不被删、无临时目录残留；同秒同名生成不同文件；`delete_backup` 越界拒绝且不误删；页面清空列表后删除按钮必须禁用；`QDesktopServices.openUrl` 必须收到 `QUrl` 实例（假实现捕获断言）；英文界面列表不含中文；无配置目录时恢复按钮禁用且 tooltip 说明原因。

**验证**：
- 确定性强杀测试（800 文件、恢复进度 2% 时 `SIGKILL`）：目标目录 801 个文件与 KEEP 标记全部保留；重跑恢复 `rc=0`、600/600 文件、临时目录自动回收。
- 端到端：备份 → 同秒二次备份（两个文件）→ 覆盖恢复 → 清空恢复 → 导出 → 导入（自动选中）→ 删除（按钮态复位），全部通过。
- 完整冒烟：见下方执行结果。

---

## v1.0.24 — 外观页「我的主题」增加删除按钮

**用户诉求**：导入/自行放入的主题越来越多（截图里「我的主题（10）」），但卡片上只有「切换」，没有删除入口；想清掉不想要的主题只能点「打开主题目录」到文件管理器里手删，路径深、易删错。

**设计决策**：

| 决策 | 结论 | 理由 |
|---|---|---|
| 按钮出现在哪些卡 | 仅 `source == "user"`（「我的主题」标签页） | 内置主题（Mellow 系列 + 自撰三款）随安装包发布，删掉下次启动/切换又从 `resources/bundled_themes` 复制回来，给按钮只会让用户白忙 |
| 当前使用的主题能否删 | **能** | 用户想清理的往往正是正在用的这款；藏起按钮等于把最常见需求堵死，改为确认框点明后果 |
| 当前主题删掉后落到哪 | 先回「本安装器改写前的原件主题」(`delete_fallback_theme()`)，不可用才退到编译内置 `default` | 删掉不喜欢的主题，用户期待的是「回到我原来的样子」；直接甩回 fcitx5 裸 default 观感突兀，还会让人觉得「删完变丑了」 |
| 操作顺序 | **先改写 classicui.conf → 再删目录** | 反过来一旦写配置失败，conf 就指向了已删主题（候选窗掉回硬编码默认且再也点不到那款） |
| 是否重启 fcitx5 | 只在「删的正是当前主题」时重启（`reset_theme=True` → 2 步任务） | 删一款没在用的主题不该打断用户正在输入 |
| 删除前的校验 | `valid_theme_name` + 必须是 user_dir 直接子目录 + 目录内必须有 theme.conf + 拒绝内置主题；符号链接只删链接 | 主题目录是用户自己丢文件夹的地方，宁可拒绝也不误删（`../escape`、`/etc`、`a/b`、无 theme.conf 的普通目录全部拒） |

**实现**：

1. **核心**（`core/appearance.py`）：新增 `DEFAULT_THEME_KEY`、`delete_user_theme(key, user_dir)`、`delete_fallback_theme(...)`、`build_theme_delete_steps(..., reset_theme)`；拒绝非法名/内置主题/越界路径/无 `theme.conf` 目录（`ValueError`/`FileNotFoundError`），符号链接只 `unlink` 链接本身。
2. **CLI**（`tools/appearance_apply.py`）：新增 `--delete-theme <key>` 与 `--reset-theme-to-default`；退出码 3 = 不可删（非法/内置/不存在），4 = 磁盘写失败。
3. **卡片**（`ui/pages/appearance_page.py`）：`_ThemeCard` 页脚新增 30×30 无边框图标按钮（`TransparentToolButton` + `FluentIcon.DELETE`，图标 15px），位于「切换」左侧；当前态卡片保留删除按钮（与「当前使用」徽标并存）；`busy`/状态机禁用时同步禁用。新增 `delete_requested` / 页面 `theme_delete_requested` 信号。
4. **确认框**（`AppearancePage.confirm_delete` 静态方法）：显示将被删除的**真实路径**，删的是当前主题时追加警示（会切回之前用的主题并重启 fcitx5）。取消则不发意图。
5. **主窗口**（`ui/main_window.py`）：`_on_theme_delete()` 以 `current_theme_key()` **实际反读**判断是否在用（不信任页面缓存，用户可能用 fcitx5-configtool 改过主题）；`appearance-theme-delete` 任务完成后按是否回退给「已删除 / 已切回之前主题」两种提示。
6. **i18n**：`appearance.theme_delete_tip` / `delete_title` / `delete_target` / `delete_note` / `delete_current_warn` / `msg.theme_deleted(_hint(_reset))` 中英双语。
7. **补漏**：`tools/rerun_flow_checks.py` 补 `RIME_INSTALLER_NO_RESTART=1`——此前只有整轮冒烟设了这条，补跑脚本跑到 `appearance_flow` 会真的 `pkill fcitx5` 杀掉开发机正在用的输入法。

**冒烟新增断言**：
- `check_appearance`：CLI 删除非当前主题（conf 不动）/ 当前主题（回原件主题；原件不存在则退 default）/ 越界·内置·不存在三类拒绝；`delete_user_theme` 正常删除 + 11 种非法 key 拒绝 + 无 `theme.conf` 目录拒绝且不误删；`build_theme_delete_steps` 步数（2 步含重启 / 1 步不重启）与 argv 开关。
- `check_appearance_ui`：内置卡删除按钮隐藏、用户卡显示且可用；隐藏按钮被程序化点击**不发**意图；点删除只发 `theme_delete_requested` 且不触发切换（事件不冒泡到整卡）；确认框取消不发意图；`busy` 时全部禁用。
- `check_appearance_flow`：主窗口删除非当前主题（目录消失、conf 不动）与当前主题（conf 不得仍指向它）。

**验证**：
- 真实链路（子进程 CLI）：切换 → 删除当前主题 → conf 落到 `prev-theme`、该目录消失、`prev-theme` 保留、`Font="Sans 12"` 等其他配置行完好；原件主题不存在时落 `default`；备份仍保留最初原件。
- 拒绝用例 5/5 命中（`../escape`、`default`、`rime-installer-ink`、无 `theme.conf` 目录、不存在的主题），被拒目录未被改动。
- `tools/rerun_flow_checks.py appearance theme_import` 与 `appearance_ui appearance_flow` 两批全绿。


---

## v1.0.25 — 全新应用图标（韵 · R）

**用户诉求**：项目根目录提供了 `New-Logo.png`（navy 底 / 米白面板 / 毛笔「韵」叠金色衬线「R」），要求用它替换现有的「下载箭头」图标。

**处理链路**（源图 1016×981，无 alpha、带生成式渐晕与斜射高光，不能直接缩放）：

| 步骤 | 做法 | 理由 |
|---|---|---|
| 定边界 | 取 `872×872+72+57` | 源图非正方形且四周留白不均；以奶白面板（127,112 起）+ 已知边饰厚度反推外框，左右各留 72、上下各留 56/57，居中 |
| 抠圆角 | 生成 200px 半径（22.9%）圆角矩形状 mask → `CopyOpacity` | 源图没有透明通道，圆角外是 navy 背景；不抠就会在浅色桌面上露出四个方形暗角。半径按奶白面板圆角（≈181）+ 边饰厚度反推 |
| 缩尺寸 | Lanczos + `-unsharp 0x0.6+0.55+0.02`，逐档直出 | 逐档从 872 主图缩，不从 256 再缩，避免二次插值累积模糊；轻锐化补回缩图后的笔画对比 |
| 小尺寸 | 16/24 先内缩 6%（`820/872`）再缩 | 边饰占宽约 6%，直接缩到 16px 后边框吃掉 1px、米白区只剩 10px，「韵」字糊成一团；内缩后边框更细、字更大 |
| 输出 | `PNG32:` 强制 RGBA，八档 16/24/32/48/64/128/256/512 | 16 档若走调色板（color_type=3）透明信息落在 tRNS，跨环境渲染容易出白角 |

**实现**：

1. `packaging/icons/rime-installer-{16,24,32,48,64,128,256,512}.png` 全部替换/新增（旧图标备份在 `.smoke-tmp/icon-backup/`，勿随包分发）。
2. `packaging/build-deb.sh`：hicolor 安装清单由五档扩到八档——面板/任务栏/Alt-Tab 按实际显示像素取图，缺档时桌面会拿 48 或 64 硬缩。
3. `config.VERSION` → `1.0.25`（`APP_ICON` 仍指 64 档，窗口标题栏与 `main.py` 的 `setWindowIcon` 自动跟随，无需改代码）。
4. `smoke_test.py`：新增 `check_icon_set()` 并于 `check_resources()` 内调用——读 PNG 头（IHDR 前 26 字节，不引 Pillow）校验八档齐全、尺寸与档位一致、≥32 档必须是 RGBA；再校验 `desktop` 的 `Icon=` 与打包脚本 `for size in ...` 的档位清单与 `ICON_SIZES` 完全一致（漏档不报错、只会让图标发糊，必须显式钉死）。版本断言同步 1.0.25。

**验证**：
- `identify` 八档尺寸/通道全部符合预期；512 档四角 `srgba(0,0,0,0)`（真透明）、中心不透明。
- 视觉复核：浅色/深色任务栏、96/64/48/32/24/16 各档单独放大检查（`preview-icon-v1025.png`）。
- `check_icon_set` 正向通过；反例（删 32 档 / 把 64 档改成 RGB / 改 build-deb 档位）均能报错。

## v1.0.26 — 环境修复页功能审计修复（假成功 / 崩溃 / 进度错页）

**用户诉求**：检查「环境修复」页所有功能是否正常、是否匹配当前版本、能否在 Deepin 与 Ubuntu 下工作。

**审计结论**（只读 + 离屏取证，真机 Deepin 25 / DDE / X11 / fcitx5-rime 5.1.10；截图 8 项全绿与实测一致）：

| 级别 | 问题 | 证据 |
|---|---|---|
| P0 | 重新部署在 Ubuntu 上「删掉可用 build + 报成功」 | `rime_deployer` 属 `librime-bin`，noble 的 `ibus-rime` / `fcitx5-rime` **都不依赖**它（包依赖与文件清单已核）；旧脚本先 `rm -rf build`，deployer 缺失时只重启框架、`echo 已完成`、退出码 0 |
| P1 | 配置目录不可读时体检抛 `PermissionError` | `Path.is_dir()` / `iterdir()` 在 3.12 与 3.13 均抛；`run_checks` 跑在 `bind()` 槽链里 → PyQt6 abort（无弹窗） |
| P1 | 修复进度 / 取消跑到「方案市场」页 | `_target_page()` 对 doctor-\* 任务回落 scheme_page；`doctor_page.set_busy()` 全项目无人调用（离屏实测：方案市场页取消变可用，体检页进度区 isHidden=True） |
| P1 | 非 GNOME 桌面用 ibus 误报「GNOME 输入源未添加」 | DDE 上 `gsettings get ... sources` 返回 `@a(ss) []`、exit 0，旧实现只看 gsettings 能否跑 |
| P1 | `run_im fcitx5` 被判「与当前前端不一致」 | 旧解析取首行含 `im` 的内容，命中注释 `# im-config(8) generated on ...` |

**本次落地（1 + 3 + 4）**：

1. **部署不再假成功、不再破坏现状**（`core/deploy_guide.py`）
   - 编译到 `<build>.rime-new`，退出码 0 且**非空**才 `rm -rf build` 后替换；失败保留原 build。
   - `find_deployer()`：PATH + `/usr/bin`、`/usr/local/bin`、`/usr/lib/ibus-rime` 三处绝对路径兜底（.desktop 启动 PATH 可能被裁剪）。
   - 缺 deployer 时**不删任何文件**，改由框架重启触发部署并轮询 8s；仍无 build 则 `exit 1` → runner 判失败 → `Step.hint_key="msg.deploy_failed"` 给定向 InfoBar（Ubuntu 装 librime-bin / 切换一次 Rime）。
   - 新增 `RIME_INSTALLER_NO_RESTART=1` 短路：测试模式不重启框架（真机教训：真跑部署脚本时 `pkill -x fcitx5` 把开发机输入法杀了）。
2. **体检 IO 全面防崩**（`core/doctor.py` / `core/state_machine.py`）
   - `_has_build` / `_child_count` / `_dir_readable` / `_is_dir` 四个安全探测；目录不存在 → 「尚不存在」WARN，无权限 → 「无读取权限」WARN + `chown` 建议，两者都不再抛异常。
   - `_gnome_input_sources(desktop)` 先判 `GNOME`；非 GNOME 给 INFO「由桌面自带输入法模块管理」且**不生成** gsettings 修复步骤。
   - `~/.xinputrc` 只认 `run_im` 指令行（跳过注释），新增 `run_im none` → INFO 分支。
3. **进度回到环境修复页**（`ui/main_window.py`）
   - 新增 `_DOCTOR_TASKS`，`_target_page()` 对 `doctor-fix / frontend-restart / frontend-redeploy / config-tool` 返回 `doctor_page`。
   - `_on_task_finished()` 补 `doctor_page.set_busy(False)`，进度区/条纹/取消按钮成对收尾。

**验证**：
- 部署脚本三路径实测：deployer 失败 → `exit 1` 且旧 build 保留、staging 无残留；缺 deployer → `exit 1` 且未删任何文件；真实 169MB 配置 → `exit 0`，build 29 个文件（17 个 `.bin`），staging 已清理。
- 进度归属实测：点「一键修复」后 `_target_page()` = DoctorPage，体检页显示「准备中」+ 取消可用，方案市场页状态为空、取消禁用。
- 冒烟：`check_doctor` / `check_deploy_guide` / `check_state_machine` / `check_i18n` / `check_detector` / `check_resources` / `check_window` 全通过（`RIME_INSTALLER_NO_RESTART` 两位取值各跑一轮）。
- 新增断言：`.rime-new` staging、失败必须 `exit 1`、`rm -rf "$BUILD"` 不得出现在 deployer 判断之前、测试模式脚本不得含 `pkill`、非 GNOME 不写 gsettings、`run_im` 解析、目录不可读不抛异常、doctor-\* 任务的目标页。

### v1.0.26 第二步 — 环境变量修复真正落地 + 文案/i18n 清理

**问题（审计证据）**：旧实现把环境变量写进 `~/.xprofile`，但 Debian/Ubuntu 的 `/etc/X11/Xsession` 只 source `~/.xsessionrc`（`USERXSESSIONRC=$HOME/.xsessionrc`），全盘无 xprofile 引用；GNOME/GDM 与 Deepin 的 `dde-session` 也不读。更关键的是 `run_checks` 读的是**当前进程**的 `os.environ`，写完文件值不变 → 该项永远红着，用户只看到「点了修复没反应」。

**落地**：

1. **新增 `core/im_env.py`**（环境变量配置的读写）
   - 写两处互补位置：`~/.config/environment.d/90-rime-im.conf`（systemd user manager 生成器读取，覆盖 GNOME/KDE Wayland）与 `~/.xsessionrc`（lightdm `session-wrapper=/etc/X11/Xsession` 会 source，覆盖 Ubuntu X11 与 **Deepin 25 X11**）。
   - 实证补充：Deepin 的 `/etc/X11/Xsession.d/00deepin-dde-env` 在把 systemd 环境导入会话时**排除**了 `XMODIFIERS`/`GTK_IM_MODULE`/`QT_IM_MODULE`/`SDL_IM_MODULE`，所以 DDE 上真正生效的是 `.xsessionrc` —— 这正是两处都要写的原因。
   - `.xsessionrc` 用标记段（`# >>> rime-installer >>>`）幂等写入；`environment.d` 片段为安装器独占文件，整文件覆盖。
   - 顺手清理旧版写在 `~/.xprofile` 的标记段：awk 只删「标记行 + 紧随的 4 个 export」，保留用户其他内容（旧实现用无差别 sed 删过同名变量）。
   - 写完调 `dbus-update-activation-environment --systemd`，让当前会话中**新启动**的程序立刻拿到正确值（失败静默跳过）。
2. **体检项区分「已写入待生效」**（`core/doctor.py`）
   - 新增 `detect_state()` 三态：`missing` / `stale`（换过前端或被手工改）/ `ok`。
   - 会话变量仍冲突但配置已就绪 → **WARN**「重新登录后生效、不标可修复」，不再无限 BAD（用户不会反复点）；配置指向别的前端 → BAD + 「一键修复会改写为本前端」。
   - 无框架运行（INFO）时：配置已就绪不再标可修复，避免无意义写入。
3. **无引擎场景不再误导**：`frontend_running` 拆成「引擎已装未运行」（WARN + 可拉起）/「引擎未装」（BAD + 引导装引擎）；`input_source` 的 GNOME 分支 `fixable` 改为 `engine_packages["ibus-rime"]`，没装 ibus-rime 就不往桌面写一个指向空引擎的输入源。
4. **文案对齐 v1.0.26 行为**
   - `doctor.guide` 删掉「重启框架会同时触发 rime 自动部署」（v1.0.6 已实证相反），改为「重启框架只重启进程，不保证重新编译」。
   - `msg.fix_hint` 改为写入 `~/.config/environment.d` 与 `~/.xsessionrc`。
   - `autostart` 的 im-config 引导补充「缺少 zenity/kdialog/whiptail 的桌面无法运行 im-config」（Deepin CLI 实测）。
5. **体检结果 i18n**（英文界面此前八行全是中文）
   - `CheckResult` 增加 `i18n_key` / `i18n_params`；中文仍由 `core/doctor.py` 字面量提供，**英文只在 `EN_TEXT` 补**，缺键由新增的 `i18n.tr_or(key, fallback)` 回退——同一句中文不必维护两份。
   - `ui/pages/doctor_page.py` 新增 `localized()`，`CheckRow` 的标题也随语言切换（旧实现只更新详情/建议，标题永远中文）。
   - `EN_TEXT` 新增 60 余条 `doctor.check.*`（8 个标题 + 各分支 detail/advice）。
6. **i18n 重复键清理**：`doctor.restart` / `doctor.redetect` / `doctor.config_tool` 在 `ZH_TEXT` 与 `EN_TEXT` 里各定义两次（后者静默覆盖），统一到「体检页」区段；`smoke_test.check_i18n` 新增 AST 级重复键检测（支持 `AnnAssign`，`EN_TEXT` 带类型注解）。

**验证**：
- `core/im_env.py` 六项实测（临时 HOME，零污染）：首次写入、重复写入幂等、用户自定义 `.xsessionrc` 内容保留、旧 `.xprofile` 段只删自己那 4 行（`foo=bar`/`baz=qux` 保留）、换前端后判 `stale`、空 HOME 判 `missing`。
- 英文覆盖：五种场景（fcitx5 正常/无引擎/无框架、ibus+GNOME、ibus+DDE）+ 目录不可读分支，`localized()` 输出零中文（仅路径参数本身含中文）。
- 冒烟：`check_doctor` / `check_i18n` / `check_deploy_guide` / `check_state_machine` / `check_window` 全通过。
- 新增断言：`environment.d` + `90-rime-im.conf` + `.xsessionrc` 必须出现、`.xsessionrc` 必须用 `export`、三态检测往返、重复写入不堆叠、已写入待生效判 WARN 且不 fixable、每个 `i18n_key` 的 title/detail/advice 都有英文、i18n 字典无重复键。

## v1.0.27 — 环境修复页新增「写入登录配置」次要入口

**用户诉求**：OK 状态下也给一个「写入登录配置」的次要入口（原话：「写一个吧，毕竟这种需要权限的还是需要用户输入密码才能操作」）。

**为什么需要**：环境变量一旦写对，`im_env` 一行变绿就不再出现在「一键修复」里——可用户仍可能想重写一份（换过前端、手工改过 `~/.xsessionrc`、从别的机器恢复备份）。改前除「体检变红」外没有任何手动入口。

**落地**：

1. `ui/pages/doctor_page.py`
   - 工具栏新增 `login_button`（`FluentIcon.SAVE` +「写入登录配置」）与信号 `write_login_requested`；**与体检结果无关**：OK / pending / BAD 都可点（busy 除外，`set_busy` 一并禁用）。
   - 提示文案随状态切换：未就绪 → 「写入 environment.d 与 ~/.xsessionrc（下次登录生效，幂等）」；已就绪 → 「点此可重写一份（换过前端或手工改过配置时用它同步）」，避免用户误以为只有出问题才能点。判据 `_im_env_ready()` 取自体检结果（`im_env` 不需要修复），UI 不再读一次磁盘。
2. `ui/main_window.py`
   - 新增 `_on_write_login()`：先弹 `MessageBox` 确认（列出两个目标路径、生效时机、撤销方式），确认后复用 `build_fix_steps("im_env")` 起 `doctor-write-login` 任务。
   - `_DOCTOR_TASKS` 加入 `doctor-write-login` → 进度/取消仍落在环境修复页；成功后 InfoBar `msg.login_written`（含 `{conf}` 实际路径）。
3. `core/im_env.py`：新增 `login_targets()`，确认框与文案共用路径，避免 UI 层再写一份。
4. `core/state_machine.py`：新增 `write_login_env` 规则，与 `doctor_fix` 同组（不依赖引擎，非 busy 即可用）。
5. i18n：`doctor.write_login` / `write_login_tip` / `write_login_tip_ok`、`dialog.write_login_title/body`、`msg.login_written(_hint)`，中英各一份。
6. **英文工具栏收短**（第 6 个按钮带来的宽度回归）：`doctor.restart` → `Restart framework`、`doctor.config_tool` → `IM configuration`（完整说明留给 tooltip，guide 文案同步改口）。页面最小宽度回到 **948**（改前 938，本次未加按钮时为 1115）。

**权限说明**：两个目标文件都在 `$HOME` 下（`~/.config/environment.d/90-rime-im.conf`、`~/.xsessionrc`），写入**不需要管理员密码**（不弹 pkexec）——它改的是「下次登录才生效」的环境变量，因此仍用确认框把路径 / 影响 / 撤销写清楚，用户点「确定」才执行。系统级 `/etc/environment.d/`（需 polkit 授权）本次**未纳入**，如确有需要可后续单独加。

**验证**：
- 离屏渲染 1180×770 中英文：6 个按钮单行不挤、引导文案不被压缩（`design/mockups/doctor-window-{zh,en}.png`）；940 宽窗口下中文仍完整，英文长文案按既有方式压缩（非本次引入）。
- 冒烟 `check_doctor` / `check_i18n` / `check_deploy_guide` / `check_state_machine` / `check_window` 全通过。
- 新增断言：按钮存在且恒可用、busy 时禁用、提示随配置状态切换、`write_login_env` 规则可用、任务步骤同时含 `environment.d` 与 `.xsessionrc`、`doctor-write-login` 的目标页为体检页、取消确认框不起任务、确认后 `_task_kind` 正确、英文下按钮文案为 `Write login config`。

**构建（v1.0.27 出包）**：`bash packaging/build-deb.sh` → `build/rime-installer_1.0.27_amd64.deb`，13.7 MB / 328 条目（v1.0.25 为 14.9 MB / 345 条目）。本次顺手清理了长期混进包内的开发期文件——`design/`（设计稿渲染 5 MB）、`smoke_test.py`、`packaging/`（图标与 desktop 的副本）、`tools/{rerun_flow_checks,preview_settings_mockup,render_doctor_page}.py`、根 `.gitignore`；`tools/` 只保留被应用以子进程调用的 `appearance_apply` / `skin_apply` / `snapshot_cli`（已核对运行时引用，其余三个无任何引用）。校验：`.venv` / `__pycache__` / `*.pyc` / `rayburst-main` / `design` / `smoke_test` / `packaging` / `Trash` 条目数全为 0；八档图标齐全；`Depends` 未变；`check_resources` / `check_doctor` / `check_i18n` / `check_deploy_guide` / `check_state_machine` / `check_window` 全绿。


## v1.0.28 — 设置页三段式改版（修「软件 Logo 高清图标不显示」）

**用户诉求**：最新版本里，设置中之前添加的软件 Logo 高清图标不显示了（截图确认 Hero 位置只剩文字）。

**根因（两层叠加，缺一不可）**：

1. **图标档位只有 64**：旧设置页把图标混在 `add_row` 平铺的信息里，取 `config.APP_ICON`（`packaging/icons/rime-installer-64.png`）。2x / 2.7x 屏上 88px 的显示尺寸需要 ~176–240 物理像素，64 档放大必糊——即使显示出来也不叫「高清」。
2. **打包把整目录排除了（真正让它「消失」的原因）**：v1.0.27 清理入包清单时写了 `--exclude='./packaging'`，把 `packaging/icons/` 一起踢出包。装机后 `config.PROJECT_ROOT/packaging/icons/*.png` 路径不存在，`QIcon` 对不存在的文件**静默返回空 pixmap**——不抛异常、不打日志，界面只是少一块图。源码树里跑一切正常，所以本地验证发现不了。

**落地**：

1. `config.py`
   - `VERSION` 1.0.27 → 1.0.28。
   - 新增 `APP_LOGO = PROJECT_ROOT / "packaging" / "icons" / "rime-installer-512.png"`：Hero 专用源图，与 hicolor 的 512 档共用同一个文件（不新增资源，避免两份图不一致）。
2. `packaging/build-deb.sh`：`--exclude='./packaging'` 拆成 `--exclude='./packaging/build-deb.sh'` + `--exclude='./packaging/rime-installer.desktop'`，**`icons/` 随应用树发布**。注释里写明原因，防止下次清理又整目录排除。
3. `ui/pages/settings_page.py` 重写为三段式（对齐 `design/mockups/settings-1180x770-default.png`）
   - **应用信息 Hero**：`_IconBadge` 固定从 512 源图按 DPR 重采样（`dpr = max(2.0, devicePixelRatioF())`，1x 屏也做超采样），88 / 72 两档（页面宽 < 900 降 72，避免卡片多长 16px）；柔和投影按主题换色（深色用纯黑，浅色用冷灰蓝）。右侧名称 + 版本胶囊 + tagline + 三列 meta（推荐前端 / 作者 / 适用系统）。
   - **界面与外观**：`StrongBodyLabel` 标题 + 分隔线 + 两行「标签 + 右对齐 200 宽下拉」，语言 / 主题各一行。
   - **apt 软件源镜像**：标题行内嵌「仅 Ubuntu」胶囊与右侧「当前源」，下接说明、镜像列表（`_MirrorList` sizeHint 跟随 minimumHeight，按项数动态算高、上限 240）、按钮行。
   - 三卡整体进 `ScrollArea`（沿用 v1.0.17 的「空间不足走滚动而非挤压控件」）；宽屏 `MAX_CONTENT = 1200` 且**整列（标题/卡片/底部说明）一起居中收缩**，避免标题贴左而卡片居中。
   - 保留全部对外契约：5 个信号（`mirror_switch_requested` / `mirror_speedtest_requested` / `theme_change_requested` / `language_change_requested` / `log_requested`）、busy 协议 `set_busy/set_step/set_progress/set_status`、属性名 `mirror_list` / `switch_button` / `speedtest_button` / `theme_combo` / `language_combo` / `mirror_note`。
   - 自绘元素（胶囊 / 分隔线 / 次要文字）在 `_fill()` 里按当前主题重刷——主窗口切主题会 `_dispatch → bind → _fill`，无需新钩子。
   - `_ElidedLabel` 兜住「适用系统」与底部非 Ubuntu 说明：`QLabel` 会按文本撑宽卡片，进而把整页最小宽度顶上去（v1.0.25 定过的老坑）。
4. `core/i18n.py` / `core/translations/en.py`：新增 `settings.prefs/mirror/mirror_only_ubuntu/current_source/mirror_hint/mirror_note/speedtest/speedtesting/switch_mirror/switching/switch_tip/speedtest_tip/tagline`、`mirror.official/mirror/untested/current`、`theme.auto_current`、`dialog.switch_mirror_title/body`。英文 `theme.auto_current` 用 `Follow system ({mode})` 而非 `(current: …)`——200 宽下拉放不下后者会被裁掉。

**顺手修（测试自身的回归）**：`smoke_test.check_skin` 断言「重新部署步必须含 `setsid`」，但 v1.0.26 让 `build_deploy_script` 认 `RIME_INSTALLER_NO_RESTART=1`（测试模式把重启换成 `echo`），于是整轮冒烟这一项**恒为 FAIL(1)**，掩盖真问题。改为：测试模式断「标注测试模式且不含 `pkill`」，正常模式仍断 `setsid`。

**验证**：
- 离屏渲染真实 `SettingsPage`（非 mock）五组：`1180×770` 中文浅色 / 中文深色 / 英文浅色、`940×640` 中文（图标降 72、出纵向滚动条）、`1600×900`（内容限宽 1200 居中）。断言：`pixmap=176×176 @dpr2`（=88 逻辑）、`72 档 → 144×144`、源图 `rime-installer-512.png`、横向滚动条 `maximum=0`。
- 冒烟 `check_resources` / `check_icon_set` / `check_i18n` / `check_theme` / `check_skin` / `check_window` 全通过；`check_window` 新增 Hero 断言（源图档位、pixmap 非空、88/72 两档物理分辨率、三卡高度 > 0、无横向滚动条），`theme_row` 断言改为 `theme_combo.currentText()`（旧行已不存在）。
- `check_deb_depends` 之外新增打包断言：`build-deb.sh` 不得出现 `--exclude='./packaging'`。
