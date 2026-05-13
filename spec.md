# Spec: winspace — Windows C 盘空间释放工具

> v0.1 草案 · 2026-05-13 · 待用户审阅

## 1. Objective(目标)

**做什么.** 一个 Windows 10/11 桌面工具,扫描 C 盘高占用目录,把可安全迁移的目录搬到其他盘,在原位置创建 NTFS Junction,让原本访问该路径的程序无感继续工作。

**为什么.** 普通用户面对"C 盘红了"通常只能删文件或者重装系统。微软建议的"移动 AppData"很复杂、易出错。市面工具(WinDirStat、SpaceSniffer)只能看不能动;能动的(FolderMove、Steam Mover)各自盯一类场景,且基本面向英文用户。winspace 把"看 + 动 + 回滚"打通,中文界面优先,默认 dry-run,降低普通用户的操作门槛和风险。

**用户.**
- 主要:中文 Windows 用户,C 盘 SSD 较小(128–512 GB),D/E 盘有富余空间
- 次要:开发者(用 CLI 批量管理 node_modules / Docker / Steam 库)

**成功是什么样.**
- 终端用户跑一次 `winspace scan` 能立刻看到"哪些目录最占地、哪些能搬"
- 一次 `winspace move` 把指定目录搬到 D 盘,原 C 盘路径下用 junction 替代,**目标程序继续正常运行**(浏览器、Steam、Docker 至少各验证一个)
- 出错或不满意可以 `winspace undo` 完整还原到操作前状态
- 在我和其他至少 1 名 Windows 用户的真机上稳定运行 1 周无数据丢失

## 2. Tech Stack

- **Python 3.11+**(PyInstaller 兼容好;`pathlib`、`shutil`、`subprocess` 已够用)
- **CLI 框架**:`click`(比 argparse 写交互流程更顺,内建 prompt/confirm)
- **测试**:`pytest` + `pytest-cov`,目标 line coverage ≥ 80%(core 模块 ≥ 90%)
- **代码质量**:`ruff`(lint + format)、`mypy --strict`(core 模块)
- **打包**:PyInstaller → 单文件 onefile exe
- **CI**:GitHub Actions,Windows runner,产出 exe artifact
- **不引入** 重依赖:不用 Pydantic / SQLAlchemy / Rich(终端输出用 `click.echo` + 极简 ANSI 即可。`Rich` 在 PyInstaller 打包后体积大,且老终端兼容性差)
- **零网络依赖**(v1 不联网。不上报、不更新、不下载)

## 3. Commands

CLI 通过 `python -m winspace` 或打包后的 `winspace.exe` 调用。所有命令默认 dry-run / 非破坏性,除非显式带 `--yes` 或 `move` 子命令。

```
winspace scan [--top N] [--min-size SIZE] [--target-drive X:] [--json]
    扫描 C 盘,列出 Top-N 占用大的目录,标注分类(可迁移/需确认/不可动)
    --top N            只显示前 N 项 (默认 30)
    --min-size SIZE    阈值,如 500MB、2GB (默认 200MB)
    --target-drive X:  指定目标盘以预检空间 (默认自动选最大空闲)
    --json             机器可读输出,供 GUI / 脚本消费

winspace move <source-path> --to <drive>: [--yes] [--dry-run]
    将单个目录移到 <drive>:\winspace\... 并建立 junction
    --yes              跳过二次确认 (脚本用)
    --dry-run          只打印将要做的事,不执行

winspace move --plan <plan.json> [--yes]
    根据 scan --json 输出的计划文件批量迁移 (交互式逐条确认,除非 --yes)

winspace undo [<entry-id> | --last | --all] [--yes]
    根据 manifest 回滚一项 / 最近一项 / 全部
    反向流程:复制回原盘 → 校验 → 删 junction → 删目标盘副本

winspace list [--json]
    列出 manifest 中所有 active 条目,以及健康状态:
      - active   junction 存在 + 目标可访问
      - broken   junction 存在但目标丢失 (告警)
      - rolled-back 已回滚 (历史记录)

winspace doctor
    自检:NTFS / 盘符可写 / 已有 junction 健康 / 是否有跨工具冲突
    不修改任何东西
```

### 风险分级(scan 输出 + move 默认行为)

每个 Detector 把候选目录标 4 个等级之一,影响 CLI 默认行为:

| RiskLevel | 含义 | scan 显示 | move 默认 |
|---|---|---|---|
| `SAFE` | 缓存/可再生(浏览器 cache、pip cache、node_modules、TEMP) | ✅ 显示并预选 | 单条确认即可 |
| `CONFIRM` | 内容重要但应用感知度低(Steam 库、Docker 数据、WSL 镜像) | ✅ 显示不预选 | 强制二次确认 + 告知用户应用需要的前置操作(关闭程序等) |
| `RISKY` | 用户数据混合(微信/QQ/Discord 等 IM 本地数据) | ⚠️ scan 默认隐藏(`--include-risky` 才显示) | 拒绝执行,除非 `--i-know-what-im-doing` |
| `NEVER` | 系统目录、云同步目录、加密卷 | ❌ 不显示 | 拒绝执行,无 override |

### 退出码

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 用户拒绝 / 主动取消 |
| 2 | 输入参数错误 |
| 3 | 权限不足 (建议提示并退出,不强升权) |
| 4 | 目标盘空间不足 |
| 5 | 操作中途失败,已尝试回滚 |
| 6 | 操作中途失败,**且回滚也失败**(严重,需用户人工处理,manifest 标记 broken) |

## 4. Project Structure

```
winspace/
├── pyproject.toml              # 项目元数据 + 依赖 + ruff/mypy/pytest 配置
├── README.md
├── spec.md                     # 本文档
├── plan.md                     # 实施计划 (下一阶段产出)
├── tasks.md                    # 任务清单 (再下一阶段)
├── .gitignore
├── .github/workflows/
│   └── ci.yml                  # lint + type + test + 打包 exe
├── src/winspace/
│   ├── __init__.py
│   ├── __main__.py             # python -m winspace 入口
│   ├── cli.py                  # click 命令组定义
│   ├── version.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── scanner.py          # 目录大小扫描,Top-N 排序
│   │   ├── mover.py            # 移动主流程:复制→校验→删源→junction
│   │   ├── junction.py         # mklink /J 抽象,Junction 创建/检测/删除
│   │   ├── manifest.py         # JSON manifest 读写 + 校验
│   │   ├── safety.py           # 黑名单、占用检测、健康检查
│   │   ├── verify.py           # 复制后校验:文件数、累计大小、树 hash
│   │   └── fs.py               # FileSystem 抽象,便于单测注入
│   ├── detectors/              # 插件式分类器(每个识别一类可迁移目录)
│   │   ├── __init__.py
│   │   ├── base.py             # Detector 基类,RiskLevel 枚举
│   │   ├── downloads.py        # %USERPROFILE%\Downloads
│   │   ├── temp.py             # %TEMP% 中超过 N 天的文件 (SAFE)
│   │   ├── node_modules.py     # 各项目下的 node_modules (SAFE)
│   │   ├── package_caches.py   # pip/npm/yarn/pnpm/cargo/gradle/maven 全局缓存 (SAFE)
│   │   ├── browser_cache.py    # Chrome/Edge/Firefox 的 Cache/Code Cache 子目录 (SAFE)
│   │   ├── ide_cache.py        # VS Code Cache/CachedData、JetBrains caches/index (SAFE)
│   │   ├── gpu_cache.py        # NVIDIA GLCache/DXCache、AMD/Intel 同类 (SAFE)
│   │   ├── creative_cache.py   # Adobe Media Cache、Unity 资源缓存 (SAFE)
│   │   ├── media_app_cache.py  # Spotify Storage、Apple Music 等 (SAFE)
│   │   ├── steam.py            # Steam library (CONFIRM,需告知 Steam 客户端)
│   │   ├── epic.py             # Epic Games Library (CONFIRM)
│   │   ├── gog.py              # GOG Galaxy Library (CONFIRM)
│   │   ├── battlenet.py        # Battle.net Library (CONFIRM)
│   │   ├── docker.py           # Docker Desktop 数据目录 (CONFIRM,需停 Docker)
│   │   └── wsl.py              # WSL 镜像目录 (CONFIRM,需 wsl --shutdown)
│   └── i18n/
│       ├── __init__.py         # locale 选择 + t() 函数
│       ├── zh_CN.py            # {key: 中文}
│       └── en_US.py            # {key: English}  错误信息双语时附加
├── tests/
│   ├── conftest.py             # 共享 fixture:tmp 文件系统、假 manifest 等
│   ├── unit/
│   │   ├── test_scanner.py
│   │   ├── test_safety.py
│   │   ├── test_manifest.py
│   │   ├── test_junction.py    # 用 mock,不真创建 junction
│   │   └── test_detectors/
│   └── integration/
│       ├── test_move_undo_roundtrip.py  # 在 tmp_path 模拟两个"盘"做完整流程
│       └── test_cli.py                  # CliRunner 跑命令
└── packaging/
    ├── winspace.spec           # PyInstaller spec
    └── installer.iss           # Inno Setup,Phase 2
```

## 5. Code Style

- **类型注解强制**:所有公开函数必须有完整类型注解(`mypy --strict`)
- **错误处理**:用 `WinspaceError` 子类,**禁止** 裸 `except Exception:`(打日志后必须 re-raise 或转换为已知错误)
- **路径**:一律 `pathlib.Path`,不接受 `str`(只在 CLI 边界做转换)
- **命名**:函数 `snake_case`,类 `PascalCase`,常量 `UPPER_SNAKE`,内部模块成员前缀 `_`
- **注释**:默认不写。只在"为什么这样而不是看起来更自然的另一种方式"时写
- **行长**:100 字符(ruff 默认 88 太挤,中文注释撑得快)

示例(一个 detector):

```python
# src/winspace/detectors/node_modules.py
from pathlib import Path
from .base import Candidate, Detector, RiskLevel


class NodeModulesDetector(Detector):
    name = "node_modules"

    def find(self, root: Path) -> list[Candidate]:
        # 走 root 下两层即可命中绝大多数项目根
        # 全盘 walk 太慢,且 node_modules 嵌套深的情况下也只搬最外层
        results: list[Candidate] = []
        for project in self._iter_project_roots(root, max_depth=4):
            nm = project / "node_modules"
            if nm.is_dir() and not nm.is_symlink():
                results.append(Candidate(
                    path=nm,
                    category="node_modules",
                    risk=RiskLevel.SAFE,
                    reason_zh="Node.js 依赖目录,可随时重装",
                    reason_en="Node.js dependencies, reinstallable via npm/pnpm",
                ))
        return results
```

## 6. Testing Strategy

**三层金字塔:**

1. **单元测试** (`tests/unit/`) — 覆盖 core/ 和 detectors/ 的所有分支。
   - 用 `tmp_path` fixture 构造假文件树
   - `fs.FileSystem` 抽象 + 注入,使得 mover/junction 操作可在不真改盘的情况下测试
   - 黑名单逻辑必须有专门测试(`test_safety.py::test_windows_dir_always_blocked`)

2. **集成测试** (`tests/integration/`) — 跑真实 move/undo 流程,但范围限制在 `tmp_path` 下两个子目录扮演"C 盘""D 盘"。
   - **关键回路测试**:`scan → move → list → undo → 验证 byte-for-byte 一致`
   - 在 Windows runner 上跑真 `mklink /J`(因为 Linux runner 没有 junction;CI 只在 windows-latest 上跑集成测试)

3. **手动验证清单** (`docs/manual-qa.md`,Phase 1 末做) — 在真机上必须通过:
   - 浏览器 `%LOCALAPPDATA%\Google\Chrome\User Data\Default\Cache` 迁移后浏览器仍可启动并正常缓存
   - Steam 游戏库迁移后能从 Steam 客户端启动游戏
   - Docker Desktop 数据目录迁移(需停 Docker)后 docker images 仍存在
   - undo 后 byte 完全一致(用 robocopy /MIR 反向比对)

**覆盖率门槛**:
- 整体 line coverage ≥ 80%
- `core/` 模块 ≥ 90%
- `safety.py` ≥ 95%(此模块出错代价最大)

## 7. Boundaries

### Always do

- 移动前一定写 manifest;manifest 写盘失败则整个操作中止
- 任何破坏性操作(`move` 实际执行、`undo`)在交互模式下都要二次确认
- 失败后**自动尝试回滚**到操作前状态;回滚失败再升级为严重错误(exit 6)
- 黑名单匹配检查(精确路径 + 路径前缀)在 `mover.execute()` 入口必做一次
- 所有 fs 操作走 `core/fs.py` 抽象,**禁止**模块里直接 `os.rename` / `shutil.move`
- 单元测试必须用 mock,不准实际创建 junction、不准动真盘符
- 提交代码前跑 `ruff check && ruff format --check && mypy && pytest`

### Ask first

- 引入新依赖(尤其大依赖如 PyQt/PySide、Rich、tqdm 等)
- 修改 manifest 格式(任何字段增删都是破坏性的,需要 migration)
- 增加新的 Detector(新 Detector 引入新风险面,需评审)
- 修改 `safety.py` 黑名单逻辑
- 修改 CI workflow / 打包流程
- 跨 v1/v2 边界的功能(GUI、安装器、自动更新等)

### Never do

- **永不** 直接在 `C:\Windows`、`C:\Program Files`、`C:\Program Files (x86)`、`C:\ProgramData\Microsoft`、`C:\$Recycle.Bin`、`C:\System Volume Information`、`C:\hiberfil.sys`、`C:\pagefile.sys`、`C:\swapfile.sys` 下做任何写操作(scan 时仅读取大小)
- **永不** 触碰云同步目录:OneDrive、iCloud Drive、Google Drive、Dropbox、Box、坚果云、百度网盘同步目录(检测后强制 NEVER,不进 scan 结果)。一旦把这类目录变成 junction,云端客户端会以为本地丢了所有文件,触发删除同步,**用户数据会被云端清掉**
- **永不** 触碰即时通讯本地数据:微信 Files(`WeChat Files`)、QQ Tencent Files、钉钉、飞书、Discord、Telegram Desktop、WhatsApp、Signal —— 这些目录混合了聊天记录、用户收发的不可再生文件。即使在 manifest 里登记,出错也无法恢复。如用户**确实**想移,必须 `winspace move <path>` 手动传完整路径,且需 `--i-know-what-im-doing` flag
- **永不** 触碰加密卷挂载点、BitLocker 保护卷、VeraCrypt 卷
- **永不** 跟随符号链接 / Junction 递归(防 loop;读到 reparse point 即停止下钻)
- **永不** 在没有目标盘空间预检的情况下开始移动(目标盘可用空间 < 待移动大小 × 1.1 直接退出 exit 4)
- **永不** 在源目录非空时新建 junction(必须先确认源已删干净)
- **永不** 自动升权;权限不足直接报错退出
- **永不** 联网(v1)、上报遥测、自动更新
- **永不** 跳过测试合入主分支
- 不强行 force / amend / push,所有 git 操作保守为主

## 8. Success Criteria(验收)

Phase 1 完成的定义:

- [ ] 5 个 CLI 命令(scan / move / undo / list / doctor)全部实现并有集成测试
- [ ] 至少 10 个 Detector 上线(downloads / temp / node_modules / package_caches / browser_cache / ide_cache / gpu_cache / creative_cache / steam / docker),并预留 plugin entry point;其余按 §4 列表分批落地
- [ ] **云同步目录探测**:winspace 能识别 OneDrive / iCloud / Google Drive / Dropbox / 坚果云等同步根目录,在 scan 阶段标记并强制排除
- [ ] **IM 数据目录探测**:能识别微信 / QQ / Telegram / Discord 等,默认 NEVER 不显示在结果中
- [ ] 在我本机真实场景跑通:浏览器缓存 / Steam 游戏库 / Docker 数据目录 至少各 1 次完整移动 + undo
- [ ] 覆盖率达到上述门槛
- [ ] PyInstaller 可在 GitHub Actions 出 exe artifact,运行不依赖 Python 环境
- [ ] README 包含中文使用说明 + 风险声明
- [ ] manifest 损坏 / 部分损坏的恢复流程有文档说明并测试覆盖

## 9. Decisions & Remaining Open Questions

### 已决策

| 项 | 决策 | 理由 |
|---|---|---|
| License | **MIT** | 宽松,方便他人贡献 Detector;PySide6 LGPL 与 MIT 兼容 |
| Phase 2 GUI | **PySide6** | 原生 Win 外观、PyInstaller 兼容性最好、LGPL 允许打包闭源用例 |
| Code signing | **v1 不签** | 年费 ~$100+,先以 README 显式告知 Defender 误报缓解步骤(白名单/Smartscreen) |
| Telemetry | **永远 opt-in** | v1 完全不联网 |
| Auto-update | **v1 不做** | Phase 2 视情况;不主动检查更新 |

### 仍然 Open

1. **i18n 后扩展第三方语言** — 留 entry point,v1 只内置 zh_CN + en_US
2. **Detector reason 文案的本地化策略** — Detector 检测路径用英文 hardcode(Chrome 在所有语言 Win 上都叫 `Chrome`),但展示给用户的 `reason` 走 i18n,需在 base 类设计中体现
3. **`%TEMP%` 的清理阈值** — 默认 30 天?7 天?用户可改?
4. **plugin entry point 形式** — 用 `importlib.metadata.entry_points` 标准方式,还是简单 `winspace/detectors/` 下扫描子类?后者打包友好,前者扩展性好。倾向先简单(扫描子类),Phase 2 再考虑 entry_points

## 10. Out of Scope(v1 不做)

- GUI(Phase 2)
- Inno Setup 安装包(Phase 2)
- 单文件迁移(只动目录)
- 跨盘符的"虚拟盘"或 reparse point 类高级技巧(只用 Junction)
- 多用户 / 服务模式 / 自动化定时清理
- 注册表 hack(例如改 Chrome 缓存路径)— 用 junction 已能覆盖,不引入额外注册表面
- 加密盘 / 网络盘 / OneDrive 同步目录 — 这些识别为"不可动"并跳过

---

## 审阅清单(给用户的)

- [ ] 第 1 节"目标"准确反映你的预期?
- [ ] 第 3 节命令集合 / 风险分级 / 退出码合理?
- [ ] 第 4 节扩充后的 Detector 名单是否还有要加/减的?
- [ ] 第 5 节代码风格能接受?(尤其"默认不写注释"、"行长 100")
- [ ] 第 7 节边界 Never 名单(尤其新增的云同步 + IM 本地数据)是否还有要追加?
- [ ] 第 8 节 Phase 1 完成定义(10+ Detector、云同步检测、IM 探测)是否合理?
- [ ] 第 9 节剩余 Open Questions(`%TEMP%` 阈值默认 30 天 OK?plugin 形式先做扫描子类 OK?)
