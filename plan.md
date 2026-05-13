# Implementation Plan: winspace v1 CLI Engine

> v0.1 · 2026-05-13 · 待用户审阅
> 基于 spec.md(commit 6f8134f)
> 目标:从空仓库走到"出 exe artifact、3 个真实场景验过"

## Overview

按依赖关系自底向上构建 CLI 引擎。采用**垂直切片**:先用最简单的 detector(node_modules)走通 scan/move/undo/list 全命令链路,**第一次 checkpoint** 就有可演示的真实场景。然后分 3 批扩展 detector,最后做打包与发布。

## Architecture Decisions

| 决策 | 选择 | 理由 |
|---|---|---|
| 配置层 | `pyproject.toml` 单文件 | uv/pip/poetry 都认;ruff/mypy/pytest 都能在内部配置 |
| CLI 框架 | `click` | 内建 Y/N prompt、子命令组、回调机制;比 argparse 写交互流程简单 |
| FS 抽象 | `Protocol`(PEP 544) | 比 ABC 轻量,鸭子类型对测试 mock 友好 |
| 实际 IO | `shutil` + `subprocess(robocopy/mklink)` | robocopy 比 shutil 在大目录、断点续传上稳;mklink 是创建 junction 的官方途径 |
| 校验策略 | "树指纹" = 文件数 + 累计大小 + 路径排序后的 SHA256 | 完整内容 hash 在 GB 级目录上不可接受;树指纹能抓 99% 的复制错误 |
| 长路径 | `\\?\` 前缀全程使用 | NodeJS / Java / 老 Win 不支持 MAX_PATH 270+;走 `\\?\C:\...` 绕开 |
| 测试 FS | 真 `tmp_path` + 两个子目录扮"C 盘""D 盘" | 集成测试要真的创建 junction,故 CI 用 windows-latest |
| Detector 注册 | 子类自动扫描(`detectors/__init__.py` 里 `pkgutil.walk_packages`) | v1 简单可控;Phase 2 再考虑 entry_points |

## Task List

---

### Phase 0: Foundation(2 tasks · ~半天)

#### Task 1: 项目骨架 + 工具链

**描述:** 建立 Python 包结构和质量工具配置。pyproject.toml 含 click 依赖、ruff/mypy/pytest 配置。建空 `__init__.py`、`__main__.py`、`cli.py`(只有 `winspace --version`)、`version.py`。

**Acceptance:**
- [ ] `pip install -e .` 安装成功
- [ ] `winspace --version` 输出 `winspace 0.1.0-dev`
- [ ] `python -m winspace --version` 同上
- [ ] `ruff check .` 通过
- [ ] `mypy --strict src/` 通过(在 0 个真实文件上)
- [ ] `pytest` 通过(在 0 个真实测试上)

**Verify:**
- [ ] `pip install -e . && winspace --version`
- [ ] `ruff check . && ruff format --check .`
- [ ] `mypy --strict src/winspace`
- [ ] `pytest -q`

**Files:**
- `pyproject.toml`
- `src/winspace/__init__.py`
- `src/winspace/__main__.py`
- `src/winspace/cli.py`
- `src/winspace/version.py`
- `tests/__init__.py`
- `tests/conftest.py`(空 fixture 占位)

**Dependencies:** None
**Scope:** S

---

#### Task 2: GitHub Actions CI 基础

**描述:** 在 `windows-latest` 上跑 lint + type + test。Python 3.11 + 3.12 矩阵。失败要 PR 拦截。

**Acceptance:**
- [ ] PR/push 到任意分支触发 CI
- [ ] CI 跑 ruff、mypy、pytest
- [ ] 3.11、3.12 双版本矩阵
- [ ] 失败时 README badge 显示 failing(badge 留后续 task)

**Verify:**
- [ ] 推一个故意失败的 commit,确认 CI 红
- [ ] 推一个修复 commit,确认 CI 绿

**Files:**
- `.github/workflows/ci.yml`

**Dependencies:** T1
**Scope:** S

---

### ✅ Checkpoint 0: 工具链就位

- [ ] CI 全绿
- [ ] 本地开发命令可用
- [ ] 仓库目录结构和 spec §4 一致
- [ ] 提交并推 GitHub

---

### Phase 1: Core 原子(6 tasks · ~2 天)

#### Task 3: `fs.py` —— FileSystem 抽象层

**描述:** 定义 `FileSystem` Protocol(`exists`/`is_dir`/`is_symlink_or_junction`/`iterdir`/`stat`/`mkdir`/`unlink`/`rmtree`/`copytree`/`rename`),提供 `RealFileSystem` 默认实现,以及 `tmp_path` 上层 fixture 用于测试。所有后续 IO 必须走这层。

**Acceptance:**
- [ ] Protocol 定义完整,有类型注解
- [ ] `RealFileSystem` 全方法实现,内部用 `pathlib` + `\\?\` 长路径前缀
- [ ] `copytree` 调用 robocopy,robocopy 不存在时回退 `shutil.copytree`
- [ ] 跨盘符 rename 自动降级为 copy+delete
- [ ] 单元测试覆盖率 ≥ 90%

**Verify:**
- [ ] `pytest tests/unit/test_fs.py -v`
- [ ] mypy 通过

**Files:**
- `src/winspace/core/fs.py`
- `tests/unit/test_fs.py`
- `tests/conftest.py`(加 `fs` fixture)

**Dependencies:** T1
**Scope:** M

---

#### Task 4: `safety.py` —— NEVER 名单 + 路径匹配

**描述:** 把 spec §7 Never 名单数据化(JSON 或 Python 常量)。提供 `is_never(path) -> tuple[bool, str]`(返回是否禁、命中规则名)。匹配规则:精确匹配、前缀匹配、glob、环境变量展开。覆盖率 ≥ 95%(spec §6 要求)。

**Acceptance:**
- [ ] 硬编码 NEVER 名单包含 spec §7 全部条目
- [ ] `is_never("C:\\Windows\\System32\\foo")` → True
- [ ] `is_never("C:\\Users\\xx\\Downloads")` → False
- [ ] `is_never("C:\\Users\\xx\\OneDrive\\foo")` → True(检测 OneDrive 同步根目录策略下文有 detector,这里只做硬路径)
- [ ] `is_never` 永不抛异常,坏输入返回 (True, "invalid-input")
- [ ] 单元测试覆盖率 ≥ 95%

**Verify:**
- [ ] `pytest tests/unit/test_safety.py -v --cov=winspace.core.safety --cov-fail-under=95`

**Files:**
- `src/winspace/core/safety.py`
- `tests/unit/test_safety.py`

**Dependencies:** T3
**Scope:** S

---

#### Task 5: `scanner.py` —— 目录大小扫描

**描述:** 实现递归大小计算,遇到 reparse point(symlink、junction)**不下钻**,只把它们记为"待处理引用"。支持 Top-N、min-size 阈值过滤。返回 `DirEntry` 列表(路径、累计大小、是否 reparse、子项数等)。

**Acceptance:**
- [ ] `scan(root, min_size=200*MB, top_n=30)` 返回排序后的列表
- [ ] reparse point 不递归(用 `os.lstat().st_file_attributes` + `FILE_ATTRIBUTE_REPARSE_POINT` 检查)
- [ ] 黑名单路径自动跳过(调用 `safety.is_never`)
- [ ] 大目录(模拟 1000 个文件)在 5 秒内扫完
- [ ] 单元测试覆盖率 ≥ 85%

**Verify:**
- [ ] `pytest tests/unit/test_scanner.py -v`
- [ ] 性能测试用 100MB 假目录,wall time < 5s

**Files:**
- `src/winspace/core/scanner.py`
- `tests/unit/test_scanner.py`

**Dependencies:** T3, T4
**Scope:** M

---

#### Task 6: `junction.py` —— mklink /J 封装

**描述:** 调 `cmd /c mklink /J <link> <target>` 创建 junction。检测函数:用 `os.stat().st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT` 判定。读取 junction 目标(`pathlib.Path.readlink` 在 3.11+ 支持)。删除 junction(`os.rmdir` 即可,不会触及目标内容)。

**Acceptance:**
- [ ] `create_junction(link, target)` 失败抛 `JunctionCreateError` 含 stderr
- [ ] `is_junction(path)` 区分 junction / 普通目录 / symlink
- [ ] `read_junction_target(path)` 返回绝对 Path
- [ ] `delete_junction(path)` 只删 junction,不动 target
- [ ] **单元测试用真实 junction**(集成性质,只在 Windows 跑;Linux runner 跳过此 test 文件)
- [ ] 含一个"target 不存在时 create 是否成功"的测试(实际 Windows 上 mklink /J 允许 target 不存在,但我们应该 reject)

**Verify:**
- [ ] `pytest tests/unit/test_junction.py -v -m windows`
- [ ] 手动:`winspace doctor` 之后能列出测试 junction(尚未实现 doctor,临时用 python 脚本)

**Files:**
- `src/winspace/core/junction.py`
- `tests/unit/test_junction.py`

**Dependencies:** T3
**Scope:** S

---

#### Task 7: `manifest.py` —— Manifest JSON 读写

**描述:** Manifest 存 `%APPDATA%\winspace\manifest.json`。Schema: `version`、`entries[]`(每条:`id` UUID、`timestamp` ISO8601、`original_path`、`new_path`、`size_bytes`、`file_count`、`tree_hash`、`status` 枚举)。提供 `load`、`save`、`append`、`update_status`、`find_by_id`、`validate`。损坏 manifest 自动备份成 `manifest.json.broken-<ts>` 并启用空 manifest。

**Acceptance:**
- [ ] 新机器没有 manifest 时 `load()` 返回空 Manifest 对象
- [ ] 写入是原子的(先写 `.tmp` 再 rename)
- [ ] 单条 entry 改动只写一次磁盘(append 不全量重写)—— 改:每次操作前后读写一次完整文件即可,append 语义在 API 上提供
- [ ] 损坏 JSON → 备份 + 空 manifest + 警告日志
- [ ] 单元测试覆盖率 ≥ 90%
- [ ] schema migration 占位(version=1,后续版本预留升级路径)

**Verify:**
- [ ] `pytest tests/unit/test_manifest.py -v`

**Files:**
- `src/winspace/core/manifest.py`
- `tests/unit/test_manifest.py`

**Dependencies:** T3
**Scope:** M

---

#### Task 8: `verify.py` —— 复制后树指纹校验

**描述:** 提供 `fingerprint(root) -> Fingerprint`(返回文件数、累计字节、sorted relpath 列表的 SHA256)。`compare(fp_a, fp_b)` 比较两枚指纹,返回差异详情。**不算单文件 SHA**,只算文件名/路径列表的 SHA —— 性能可控,能抓 99% 复制错误(漏文件、错名、错大小)。

**Acceptance:**
- [ ] `fingerprint(tmp_path)` 在 1000 文件目录 < 1s
- [ ] 同样目录复制到另一处,两枚 fingerprint 一致
- [ ] 故意改动一个文件大小,fingerprint 不同
- [ ] 删一个文件,fingerprint 不同
- [ ] 单元测试覆盖率 ≥ 90%

**Verify:**
- [ ] `pytest tests/unit/test_verify.py -v`

**Files:**
- `src/winspace/core/verify.py`
- `tests/unit/test_verify.py`

**Dependencies:** T3
**Scope:** S

---

### ✅ Checkpoint 1A: Core 原子全绿

- [ ] T3-T8 全部 acceptance 满足
- [ ] 全部覆盖率门槛达标
- [ ] CI 全绿
- [ ] 与用户简短同步:演示一下 fs/safety/manifest 单测输出

---

### Phase 2: 第一个垂直切片(8 tasks · ~3 天)

#### Task 9: `detectors/base.py` —— Detector 框架

**描述:** 定义 `Detector` Protocol、`Candidate` dataclass、`RiskLevel` 枚举(`SAFE/CONFIRM/RISKY/NEVER`)。`Candidate` 字段:`path`、`category`、`risk`、`size_bytes`、`reason_zh`、`reason_en`、`detector_name`、`prerequisite_note`(给 CONFIRM 用的"请先关闭 XX")。提供 `discover_detectors()` 用 `pkgutil.walk_packages` 收集所有 `Detector` 子类。

**Acceptance:**
- [ ] Protocol 接口最简:`name: str`、`find(self, fs, scanner) -> list[Candidate]`
- [ ] `discover_detectors()` 在 `detectors/` 目录下找到所有非 base 的实现
- [ ] 注册顺序确定(按文件名字母序),便于复现
- [ ] 单元测试用 mock detector 验证发现机制

**Verify:**
- [ ] `pytest tests/unit/test_detectors/test_base.py -v`

**Files:**
- `src/winspace/detectors/__init__.py`
- `src/winspace/detectors/base.py`
- `tests/unit/test_detectors/test_base.py`

**Dependencies:** T3
**Scope:** S

---

#### Task 10: `detectors/node_modules.py`

**描述:** 第一个真实 detector。从 `C:\Users\<user>\` 起以 BFS 走最多 5 层,命中 `node_modules` 子目录(且不是 symlink/junction)就收集。RiskLevel.SAFE。

**Acceptance:**
- [ ] 在假目录树(包含嵌套项目)能找到全部 node_modules
- [ ] 跳过已经是 junction 的 node_modules(避免循环)
- [ ] 跳过黑名单路径(借 safety.is_never)
- [ ] 单元测试用 tmp_path 构造目录树

**Verify:**
- [ ] `pytest tests/unit/test_detectors/test_node_modules.py -v`

**Files:**
- `src/winspace/detectors/node_modules.py`
- `tests/unit/test_detectors/test_node_modules.py`

**Dependencies:** T9
**Scope:** S

---

#### Task 11: `mover.py` —— Move 主流程

**描述:** `Mover.execute(src, dst_drive, *, dry_run=False)` 工作流(**反向保护流程,先保留源再建 junction**):
1. **预检**:NEVER 不准、src 必须存在且不是 junction、dst 可写、空间够 ≥ src × 1.1
2. **算原 fingerprint** —— 文件数、累计字节、路径列表 SHA256
3. **复制到目标** `<dst_drive>:\winspace\<basename>[-<n>]`(冲突加后缀)
4. **算新 fingerprint,比较** —— 不一致就 `rmtree` 新拷贝并抛错(此时**源完好**)
5. **rename 源** 为 `<source>.winspace-old-<ts>`(rename 失败说明源被占用,中止,清理新拷贝)
6. **创建 junction**:`mklink /J <source> <dst>`(失败的话:rmtree 新拷贝 + rename `.winspace-old-<ts>` 回 source,源完好)
7. **写 manifest entry**,status=active(此时即使后续步骤失败,空间已释放、junction 已建、有据可查)
8. **删除 `<source>.winspace-old-<ts>` 旧名**(失败的话:记到 manifest 的 `cleanup_pending` 字段,doctor 后续清理;不影响主功能)
9. 全流程异常:按反序回滚,失败本身记日志,最差不到"源数据丢失"

**关键不变量**:任何一步骤失败,**用户数据完整可见**。Junction 已建后才动旧源,确保新位置和原 access path 至少有一条永远活着。

**Acceptance:**
- [ ] dry_run 不动磁盘,只打印计划
- [ ] 全流程在 tmp_path 模拟两盘下能跑过(完整 9 步)
- [ ] 第 3 步只读 dst 失败,源完整无损,无 manifest 记录
- [ ] 第 4 步指纹不一致,新拷贝被清理,源完整,无 manifest 记录
- [ ] 第 5 步源占用导致 rename 失败,新拷贝被清理,源完整,无 manifest 记录
- [ ] **第 6 步 junction 创建失败,自动 rename `.winspace-old-<ts>` 回源**,新拷贝清理,源完好
- [ ] 第 8 步旧源 rmtree 失败 → manifest 标 `cleanup_pending=True`,主流程仍报成功
- [ ] 不变量测试:**任一步失败后**,用户访问 source path 仍能读到完整数据
- [ ] 单元测试覆盖率 ≥ 90%

**Verify:**
- [ ] `pytest tests/unit/test_mover.py -v --cov=winspace.core.mover --cov-fail-under=90`

**Files:**
- `src/winspace/core/mover.py`
- `tests/unit/test_mover.py`

**Dependencies:** T3, T4, T6, T7, T8
**Scope:** M

---

#### Task 12: CLI `scan` 命令

**描述:** `winspace scan [--top N] [--min-size SIZE] [--target-drive X:] [--json] [--include-risky]`。流程:`discover_detectors()` → 调用每个 detector.find() → 汇总 → 按 size 降序 → 应用 top_n / min_size 过滤 → 输出表格或 JSON。RISKY 默认隐藏除非 `--include-risky`。NEVER 永不出现在结果。

**Acceptance:**
- [ ] 文本输出:用户友好的表格(路径、大小可读、分类、风险、原因)
- [ ] `--json` 出机器可读,字段名稳定
- [ ] `--target-drive D:` 时在表头显示 D 盘可用空间预检
- [ ] `--include-risky` 切换 RISKY 显隐
- [ ] 中文输出为默认,bilingual reason 在 verbose 模式或 --json 同时给

**Verify:**
- [ ] `pytest tests/integration/test_cli_scan.py -v`(用 CliRunner)
- [ ] 手动:`winspace scan --top 5` 在真机出合理结果(到此应能扫到 node_modules)

**Files:**
- `src/winspace/cli.py`(增 scan 子命令)
- `tests/integration/test_cli_scan.py`

**Dependencies:** T9, T10, T5
**Scope:** M

---

#### Task 13: CLI `move` 命令

**描述:** `winspace move <source> --to <drive>: [--yes] [--dry-run]`。流程:safety 检查 → 显示计划 → 交互 Y/N(除非 `--yes`)→ 调 `Mover.execute`。出错按 spec §3 退出码退出。`--dry-run` 走预检但不执行。

**Acceptance:**
- [ ] 不带 `--yes` 时必询问 confirm
- [ ] `--yes` 跳过 confirm
- [ ] NEVER 路径直接拒绝,exit 2
- [ ] 目标空间不足,exit 4
- [ ] 单元/集成测试覆盖以上所有分支

**Verify:**
- [ ] `pytest tests/integration/test_cli_move.py -v`

**Files:**
- `src/winspace/cli.py`(增 move)
- `tests/integration/test_cli_move.py`

**Dependencies:** T11, T12
**Scope:** M

---

#### Task 14: CLI `undo` 命令

**描述:** `winspace undo [<id> | --last | --all] [--yes]`。Mover 增 `undo(entry)` 方法:删 junction → 反向复制 → 校验 → 删新位置 → manifest status=rolled_back。失败处理同 mover.execute。

**Acceptance:**
- [ ] `--last` 找 status=active 中最新一条
- [ ] `--all` 倒序逐个 undo;一条失败就停,后续不动
- [ ] 不带参时显示可 undo 列表让用户选
- [ ] undo 后源位置恢复到迁移前(byte for byte,fingerprint 一致)
- [ ] 单元/集成测试覆盖

**Verify:**
- [ ] `pytest tests/integration/test_cli_undo.py -v`

**Files:**
- `src/winspace/core/mover.py`(扩展 undo 方法)
- `src/winspace/cli.py`(增 undo)
- `tests/integration/test_cli_undo.py`

**Dependencies:** T11, T13
**Scope:** M

---

#### Task 15: CLI `list` 命令

**描述:** `winspace list [--json]`。读 manifest,逐条做健康检查(junction 还在?target 可访问?),输出列表 + 状态(active / broken / rolled_back)。

**Acceptance:**
- [ ] 输出含 id、原路径、新路径、size、status、迁移时间
- [ ] junction 缺失或 target 不存在 → 标记 broken
- [ ] `--json` 出机器可读

**Verify:**
- [ ] `pytest tests/integration/test_cli_list.py -v`

**Files:**
- `src/winspace/cli.py`(增 list)
- `tests/integration/test_cli_list.py`

**Dependencies:** T7, T11
**Scope:** S

---

#### Task 16: 集成测试 —— node_modules roundtrip

**描述:** 端到端集成测试:tmp_path 下建 `C/Users/xx/proj/node_modules`(填假文件 ~50MB),scan 找到 → move 到 D → 验证 junction 存在、target 在 D、原 C 路径访问能透明到 D → undo → 验证 byte 完全一致。

**Acceptance:**
- [ ] 测试在 `windows-latest` runner 上跑过
- [ ] 整个 roundtrip < 60 秒
- [ ] 用 fingerprint 双向比对,无差异

**Verify:**
- [ ] `pytest tests/integration/test_roundtrip_node_modules.py -v -m windows`
- [ ] CI 在 windows-latest 跑过

**Files:**
- `tests/integration/test_roundtrip_node_modules.py`

**Dependencies:** T10-T15
**Scope:** S

---

### ✅ Checkpoint 1B(里程碑):第一个端到端闭环

- [ ] T9-T16 全绿
- [ ] **真机手动验证**:在我自己机器上找一个真实 node_modules(随便一个 npm 项目即可,~ 200MB+),完整跑 scan → move → 在原位置 `ls` 看到内容(透明) → 实际看 D 盘文件 → undo → byte 一致
- [ ] 推 GitHub,打 tag `v0.0.1-alpha-walking-skeleton`
- [ ] 与用户**同步演示**,确认设计扛得住再开扩展

---

### Phase 3: SAFE 三件套补全(3 tasks · ~1 天)

#### Task 17: `detectors/browser_cache.py`

**描述:** 识别 Chrome / Edge / Firefox 的明确缓存子目录:
- `%LOCALAPPDATA%\Google\Chrome\User Data\Default\Cache`
- `%LOCALAPPDATA%\Google\Chrome\User Data\Default\Code Cache`
- `%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Cache` 等
- `%APPDATA%\Mozilla\Firefox\Profiles\*\cache2`
- 多 profile 支持(`User Data\Profile *`)

**Acceptance:**
- [ ] 多 profile 全部识别
- [ ] 不动 `User Data\Default\` 整目录(会包含书签、历史等数据)
- [ ] 单元测试

**Verify:** `pytest tests/unit/test_detectors/test_browser_cache.py -v`

**Files:**
- `src/winspace/detectors/browser_cache.py`
- `tests/unit/test_detectors/test_browser_cache.py`

**Dependencies:** T9
**Scope:** S

---

#### Task 18: `detectors/package_caches.py`

**描述:** 识别开发者全局包管理缓存:
- pip: `%LOCALAPPDATA%\pip\cache`
- npm: `%APPDATA%\npm-cache` 或 `%LOCALAPPDATA%\npm-cache`
- yarn: `%LOCALAPPDATA%\Yarn\Cache\v6` 等
- pnpm: `%LOCALAPPDATA%\pnpm\store`
- cargo: `%USERPROFILE%\.cargo\registry\cache`
- gradle: `%USERPROFILE%\.gradle\caches`
- maven: `%USERPROFILE%\.m2\repository`

**Acceptance:**
- [ ] 全部 7 个工具的路径检测
- [ ] 仅检测缓存子目录,不动配置文件
- [ ] 单元测试用 tmp 文件树

**Verify:** `pytest tests/unit/test_detectors/test_package_caches.py -v`

**Files:**
- `src/winspace/detectors/package_caches.py`
- `tests/unit/test_detectors/test_package_caches.py`

**Dependencies:** T9
**Scope:** S

---

#### Task 19: SAFE 三件套集成测试

**描述:** 一个集成测试文件覆盖 browser_cache + package_caches 的 detect → scan 出来 → move → undo,在 tmp_path 模拟。

**Acceptance:**
- [ ] 双向 fingerprint 一致
- [ ] 集成 < 90 秒

**Verify:** `pytest tests/integration/test_roundtrip_safe_trio.py -v -m windows`

**Files:**
- `tests/integration/test_roundtrip_safe_trio.py`

**Dependencies:** T17, T18
**Scope:** S

---

### ✅ Checkpoint 2: SAFE 三件套闭环

- [ ] T17-T19 全绿
- [ ] **真机手动验证**:Chrome 缓存搬到 D 盘后浏览器正常启动 + 上网 + 看到新文件落在 D
- [ ] 推 GitHub,tag `v0.0.2-alpha-safe-trio`

---

### Phase 4: NEVER detectors(2 tasks · ~半天)

#### Task 20: `detectors/cloud_sync.py`

**描述:** 检测云同步根目录,**输出 RiskLevel.NEVER 候选**(scanner 看到 NEVER 即排除整子树,**不进 scan 结果**)。识别路径与策略:
- OneDrive:`%USERPROFILE%\OneDrive*` + 读取注册表 `HKCU\Software\Microsoft\OneDrive\Accounts\*\UserFolder`
- iCloud Drive:`%USERPROFILE%\iCloudDrive`
- Google Drive:`%USERPROFILE%\Google Drive` + Drive for Desktop 的 `My Drive` 挂载
- Dropbox:`%USERPROFILE%\Dropbox` + 读 `%LOCALAPPDATA%\Dropbox\info.json`
- 坚果云:`%USERPROFILE%\Nutstore\` + 读 `%LOCALAPPDATA%\Nutstore\db\config.db`(SQLite,有路径才有依赖,优先用默认路径)
- 百度网盘同步空间:`%USERPROFILE%\BaiduNetdiskWorkspace`

**Acceptance:**
- [ ] 检测覆盖 6 类云同步
- [ ] 即使路径被用户改过,优先用注册表/配置文件中的真实路径
- [ ] 在 scanner 集成测试中验证:云目录下的文件不在结果里出现
- [ ] **单测覆盖率 ≥ 95%**(此模块出错代价大)

**Verify:** `pytest tests/unit/test_detectors/test_cloud_sync.py -v --cov-fail-under=95`

**Files:**
- `src/winspace/detectors/cloud_sync.py`
- `tests/unit/test_detectors/test_cloud_sync.py`

**Dependencies:** T9, T5(scanner 需要消费 NEVER 候选)
**Scope:** M

---

#### Task 21: `detectors/im_data.py`

**描述:** 识别 IM 本地数据目录,RiskLevel.RISKY(scan 默认隐藏,需要 `--include-risky`;move 拒绝执行除非 `--i-know-what-im-doing`)。识别:
- WeChat Files:`%USERPROFILE%\Documents\WeChat Files`、读注册表 `HKCU\Software\Tencent\WeChat\FileSavePath`
- QQ Tencent Files:`%USERPROFILE%\Documents\Tencent Files`
- 钉钉:`%USERPROFILE%\AppData\Local\DingTalk\users\*`
- 飞书:`%USERPROFILE%\AppData\Local\Lark\sdk_storage`
- Discord:`%APPDATA%\discord`
- Telegram Desktop:`%APPDATA%\Telegram Desktop\tdata`
- WhatsApp:`%APPDATA%\WhatsApp`
- Signal:`%APPDATA%\Signal`

**Acceptance:**
- [ ] 8 类 IM 全部识别
- [ ] 默认 scan 看不到这些
- [ ] move 路径不带 `--i-know-what-im-doing` 时立即拒绝,exit 2
- [ ] **单测覆盖率 ≥ 95%**

**Verify:** `pytest tests/unit/test_detectors/test_im_data.py -v --cov-fail-under=95`

**Files:**
- `src/winspace/detectors/im_data.py`
- `tests/unit/test_detectors/test_im_data.py`
- `src/winspace/cli.py`(加 --include-risky / --i-know-what-im-doing flag)

**Dependencies:** T9, T12
**Scope:** M

---

### ✅ Checkpoint 3: 安全网到位

- [ ] T20-T21 全绿
- [ ] 在真机跑 `winspace scan`,确认 OneDrive、WeChat Files 等不出现在结果里
- [ ] 推 GitHub,tag `v0.0.3-alpha-safety-net`

---

### Phase 5: 剩余 SAFE detectors(6 tasks · ~1.5 天)

每个都是 S 级,单元测试为主,可**并行**(在 T22 之后,T23-T28 之间无依赖)。

#### Task 22: `detectors/downloads.py` — Downloads 目录(SAFE)
#### Task 23: `detectors/temp.py` — TEMP 超过 30 天文件(SAFE)
#### Task 24: `detectors/ide_cache.py` — VS Code + JetBrains caches(SAFE)
#### Task 25: `detectors/gpu_cache.py` — NVIDIA/AMD/Intel shader cache(SAFE)
#### Task 26: `detectors/creative_cache.py` — Adobe Media Cache + Unity Asset Cache(SAFE)
#### Task 27: `detectors/media_app_cache.py` — Spotify Storage + Apple Music(SAFE)

每个的 acceptance 模板:
- [ ] 识别 spec §4 列表中的对应路径
- [ ] 单元测试覆盖正/负样本
- [ ] 在假 `%LOCALAPPDATA%` 子树上 detect 结果稳定

**Files(每个):**
- `src/winspace/detectors/<name>.py`
- `tests/unit/test_detectors/test_<name>.py`

**Dependencies(每个):** T9
**Scope(每个):** S

---

### Phase 6: CONFIRM detectors(4 tasks · ~1.5 天)

#### Task 28: `detectors/steam.py`

**描述:** 读 Steam 安装路径(注册表 `HKCU\Software\Valve\Steam\SteamPath`)+ 解析 `<steam>\steamapps\libraryfolders.vdf` 列出所有库目录。每个库目录单独作为 Candidate。`prerequisite_note_zh = "请先关闭 Steam,move 后从 Steam 添加新位置为库"`。

**Acceptance:**
- [ ] libraryfolders.vdf 解析覆盖单/多库情况
- [ ] Steam 未安装时正常返回空
- [ ] 单元测试用样本 vdf

**Dependencies:** T9
**Scope:** S

#### Task 29: `detectors/epic.py` + `gog.py` + `battlenet.py`(合并一个 task)

**描述:** 3 个 launcher 的库目录识别,各自从注册表 / 配置文件读取。

**Acceptance:**
- [ ] 每个 launcher 各 1 个 detector,共 3 个
- [ ] 各有单元测试

**Files:** 3 个 detector + 3 个测试文件
**Dependencies:** T9
**Scope:** M(因 3 合一)

#### Task 30: `detectors/docker.py`

**描述:** 读 Docker Desktop 设置(`%APPDATA%\Docker\settings-store.json` 或老版 `settings.json`),拿 `dataFolder`(默认 `%LOCALAPPDATA%\Docker\wsl`)。`prerequisite_note_zh = "请先 'docker desktop stop' 或在 Docker Desktop 退出"`。

**Dependencies:** T9
**Scope:** S

#### Task 31: `detectors/wsl.py`

**描述:** 跑 `wsl --list -v` 解析 distro 名,读注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Lxss\<guid>\BasePath` 拿到 ext4.vhdx 所在目录。`prerequisite_note_zh = "请先 'wsl --shutdown'"`。

**Dependencies:** T9
**Scope:** S

---

### ✅ Checkpoint 4: 所有 Detectors 落地

- [ ] T22-T31 全绿
- [ ] `winspace scan` 在真机一次出多类候选
- [ ] **真机手动验证 #2**:Steam 库迁移 + 验证客户端能识别新位置
- [ ] **真机手动验证 #3**:Docker Desktop 数据目录迁移 + 验证 docker images 仍存在
- [ ] 推 GitHub,tag `v0.0.4-alpha-all-detectors`

---

### Phase 7: doctor + i18n + 收尾(3 tasks · ~1 天)

#### Task 32: CLI `doctor` 命令

**描述:** 环境自检:NTFS / 跨盘空间 / 已存在 junction 健康 / 跨工具冲突(检测已存在的 mklink/symlink/junction 是否指向我们没记录的位置)。只读,不修改。

**Acceptance:**
- [ ] 报告分节:盘符状态 / 已知 manifest 健康 / 系统 junction 审计
- [ ] `--json` 出机器可读
- [ ] 单元/集成测试

**Files:** `src/winspace/cli.py` + `tests/integration/test_cli_doctor.py`
**Dependencies:** T7, T15
**Scope:** S

#### Task 33: i18n 框架 + zh_CN / en_US

**描述:** 简单的 `t(key, lang=None)` 函数,lang 由 `WINSPACE_LANG` 或 OS locale 推断。中文默认。错误消息双语:`{zh} / {en}` 拼接。

**Acceptance:**
- [ ] 所有 CLI 输出走 t()
- [ ] zh_CN.py + en_US.py 覆盖全部 key
- [ ] 缺 key 时 fallback en_US
- [ ] 设 `WINSPACE_LANG=en` 时全英文输出

**Files:**
- `src/winspace/i18n/__init__.py`
- `src/winspace/i18n/zh_CN.py`
- `src/winspace/i18n/en_US.py`
- 全 cli + core 改用 t()

**Dependencies:** 全部命令落地后
**Scope:** M

#### Task 34: doctor 反向审计 + manifest 修复工具

**描述:** doctor 能发现"系统里有 junction 但 manifest 里没记录"或反过来。提供 `winspace doctor --fix` 提示用户选择"领养"或"删除"孤立 junction。

**Acceptance:**
- [ ] 检测能力覆盖
- [ ] `--fix` 交互式
- [ ] 单元测试用 mock manifest 和 mock junction

**Files:** `src/winspace/cli.py` 增 --fix 子参 + `tests/integration/test_doctor_fix.py`
**Dependencies:** T32
**Scope:** M

---

### Phase 8: 打包 + 发布(3 tasks · ~1 天)

#### Task 35: PyInstaller spec + 本地构建脚本

**描述:** 在 `packaging/winspace.spec` 写 PyInstaller 配置,onefile 模式,包含所有 detectors。本地脚本 `scripts/build-exe.ps1` 一键打包,产物 `dist/winspace.exe`。

**Acceptance:**
- [ ] `dist/winspace.exe` 在干净 Win 机器(无 Python)上能跑 `winspace scan --top 5`
- [ ] exe 大小 < 50MB
- [ ] `winspace --version` 输出正确

**Files:** `packaging/winspace.spec`、`scripts/build-exe.ps1`、`pyproject.toml`(加 `[tool.pyinstaller]` 或脚本入口)
**Dependencies:** 全部命令落地
**Scope:** M

#### Task 36: CI 出 exe artifact

**描述:** 扩展 `.github/workflows/ci.yml`,在 tag 触发时打包 exe 并上传 artifact;非 tag 也每个 PR 出一份 build artifact(供人工冒烟测试)。

**Acceptance:**
- [ ] tag push 后 release artifact 出现
- [ ] PR 出 artifact

**Files:** `.github/workflows/ci.yml`
**Dependencies:** T35
**Scope:** S

#### Task 37: README + Manual QA 文档

**描述:** README 中文为主,含:简介 / 安装 / 用法 / 风险声明 / Defender 误报 FAQ / 反馈渠道。`docs/manual-qa.md` 写明 3 个真机验证场景的步骤。

**Acceptance:**
- [ ] README 至少 5 个 section
- [ ] manual-qa.md 含浏览器/Steam/Docker 三套步骤
- [ ] 含截图占位(后期补)

**Files:** `README.md`、`docs/manual-qa.md`
**Dependencies:** 全部功能落地
**Scope:** S

---

### ✅ Checkpoint 5: v0.1.0 候选

- [ ] T32-T37 全绿
- [ ] CI 出可下载 exe
- [ ] **3 个真机场景全部通过**(浏览器缓存 / Steam / Docker)
- [ ] README + 风险声明完整
- [ ] tag `v0.1.0`

---

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Junction 创建失败但源已删 | **极高**(数据丢失) | Mover 第 6 步失败必须**重新移回**,T11 acceptance 已强制覆盖 |
| robocopy/mklink 不存在或不可用 | 中 | T3 fs.py 检测;T6 doctor 命令验证 |
| Defender 把 PyInstaller exe 标红 | 中(用户疑虑) | README 写明白名单方法;v1 不签名,长期视用户量决定 |
| 长路径 > 260 字符 | 中(部分迁移失败) | 全程 `\\?\` 前缀;在 doctor 中报告系统是否启用 long path support |
| 微信目录探测错过用户自定义路径 | 中(误删/误移) | im_data.py 优先读注册表 `FileSavePath`,fallback 默认路径 |
| 测试用真 junction 在 CI 上抖动 | 低 | windows-latest runner、整测试加 `@pytest.mark.windows`、本地必跑 |
| Steam libraryfolders.vdf 格式变 | 低 | T28 acceptance 含 multi-library vdf 样本测试 |
| Python 3.11 vs 3.12 行为差异 | 低 | CI 矩阵双版本覆盖 |
| 在迁移过程中用户拔电源/重启 | 中 | Manifest 写在每个状态变更时;doctor 可发现"中间态" junction 并提示 |

## Decisions Locked(2026-05-13)

| 项 | 决策 |
|---|---|
| Mover 流程方向 | **反向 9 步**(rename 源 → 建 junction → 删旧名),见 T11 |
| `%TEMP%` 阈值 | **默认 30 天**,提供 `--temp-age-days N` 参数 |
| Steam 多库 candidate | **逐个**(每个 library 一个 candidate,用户可只移其中) |
| RISKY override flag | **`--i-know-what-im-doing`**(刻意长,制造摩擦) |

## Open Questions(剩余)

1. **跨盘 robocopy 失败 fallback 策略** — 倾向"重试 robocopy 一次,再失败就报错并保留源,提示用户用 doctor 诊断"。T11 实现时最终定。

## Parallelization

可并行:
- **Phase 5 (T22-T27)** 6 个 detector 完全独立,可同会话内并行写,也可分多个 session
- **CI (T2) 和 fs (T3)** 完成后,documentation 类任务(T37 README)可并行
- **i18n (T33)** 一旦所有 CLI 命令命名稳定就可启动,可与 Phase 6 并行

必须顺序:
- Phase 0 → Phase 1 → Phase 2(底层依赖链)
- Phase 4 cloud_sync 必须在所有真机验证之前(防误删用户云数据)
- Packaging 必须在所有功能 freeze 之后

## 估算

| Phase | 任务数 | 估计时长(全集中工作日) |
|---|---|---|
| 0 Foundation | 2 | 0.5 |
| 1 Core 原子 | 6 | 2 |
| 2 第一切片 | 8 | 3 |
| 3 SAFE 三件套 | 3 | 1 |
| 4 NEVER 安全网 | 2 | 0.5 |
| 5 剩余 SAFE | 6 | 1.5 |
| 6 CONFIRM | 4 | 1.5 |
| 7 doctor + i18n + 修复 | 3 | 1 |
| 8 打包 + 发布 | 3 | 1 |
| **合计** | **37** | **~12 工作日** |

---

## 审阅清单(给用户的)

- [ ] **垂直切片策略**(第一个 checkpoint 就有 node_modules 能跑通)合理吗?
- [ ] **Phase 4 安全网放在中间**(非最后)—— 因为它影响 scan 默认结果,真机测试前必须就位。OK?
- [ ] 任务粒度合适吗?有觉得过细或过粗的 task?
- [ ] Risks 表中 #1(Junction 创建失败但源已删)的 mitigation(重新移回)够吗?要不要在 mover 内部加"在删源之前先创 junction"的可能性?(权衡:同盘符同名冲突)
- [ ] 估算 ~12 工作日,在你预期内吗?如果想压缩,可以砍 Phase 5 部分 detector(放到 v0.2)
