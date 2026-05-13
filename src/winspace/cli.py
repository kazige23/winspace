"""winspace command-line interface.

Implements spec §3's command set:

* ``winspace scan`` — discover candidates across detectors, rank by size
* ``winspace move`` — execute the 9-step move workflow on one source
* ``winspace undo`` — reverse a previous move by manifest id / --last / --all
* ``winspace list`` — show all manifest entries and their health

Exit codes follow spec §3 §"退出码". Errors raised inside core modules
are translated to exit codes by :func:`_handle_error`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from winspace.core.errors import (
    InsufficientSpaceError,
    JunctionError,
    ManifestError,
    MoveAbortedError,
    SafetyViolation,
    VerificationError,
    WinspaceError,
)
from winspace.core.fs import RealFileSystem
from winspace.core.junction import is_junction
from winspace.core.manifest import EntryStatus, ManifestEntry, load
from winspace.core.mover import execute_move, execute_undo
from winspace.core.scanner import directory_size
from winspace.detectors.base import Candidate, RiskLevel, discover_detectors
from winspace.version import __version__

# --- spec-mandated exit codes -----------------------------------------------

EXIT_OK = 0
EXIT_USER_CANCEL = 1
EXIT_BAD_ARGS = 2
EXIT_PERMISSION = 3
EXIT_NO_SPACE = 4
EXIT_MOVE_ABORTED = 5
EXIT_MOVE_FROZEN = 6


# --- size parsing / formatting ----------------------------------------------


_SIZE_UNITS: tuple[tuple[str, int], ...] = (
    ("TB", 1024**4),
    ("GB", 1024**3),
    ("MB", 1024**2),
    ("KB", 1024),
    ("T", 1024**4),
    ("G", 1024**3),
    ("M", 1024**2),
    ("K", 1024),
    ("B", 1),
)


def parse_size(value: str) -> int:
    """Parse strings like ``"200MB"``, ``"2.5G"``, ``"512"`` into bytes.

    Plain integers are interpreted as bytes. Invalid inputs raise
    :class:`click.BadParameter` so click renders a helpful message.
    """
    raw = value.strip().upper().replace(" ", "")
    if not raw:
        raise click.BadParameter("size cannot be empty")
    for suffix, factor in _SIZE_UNITS:
        if raw.endswith(suffix):
            num_part = raw[: -len(suffix)] or "1"
            try:
                return int(float(num_part) * factor)
            except ValueError as e:
                raise click.BadParameter(f"unable to parse size: {value!r}") from e
    try:
        return int(raw)
    except ValueError as e:
        raise click.BadParameter(f"unable to parse size: {value!r}") from e


def format_size(size_bytes: int) -> str:
    """Render bytes as a human-readable string (e.g., ``"1.2 GB"``)."""
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PB"


# --- error → exit-code mapping ----------------------------------------------


def _handle_error(err: BaseException) -> int:
    """Translate a WinspaceError into the spec exit code."""
    if isinstance(err, InsufficientSpaceError):
        click.echo(f"错误 / error: {err}", err=True)
        return EXIT_NO_SPACE
    if isinstance(err, SafetyViolation):
        click.echo(f"安全拒绝 / safety violation: {err}", err=True)
        return EXIT_BAD_ARGS
    if isinstance(err, PermissionError):
        click.echo(f"权限不足 / permission denied: {err}", err=True)
        return EXIT_PERMISSION
    if isinstance(err, MoveAbortedError):
        click.echo(f"移动失败,已回滚 / move aborted: {err}", err=True)
        return EXIT_MOVE_ABORTED
    if isinstance(err, (VerificationError, JunctionError, ManifestError)):
        click.echo(f"操作失败 / operation failed: {err}", err=True)
        return EXIT_MOVE_FROZEN
    if isinstance(err, WinspaceError):
        click.echo(f"错误 / error: {err}", err=True)
        return EXIT_MOVE_ABORTED
    raise err  # let click propagate truly unexpected exceptions


# --- candidate enrichment ---------------------------------------------------


def _enrich_candidates(
    candidates: list[Candidate], min_size: int, include_risky: bool
) -> list[tuple[Candidate, int]]:
    """Compute sizes, apply filters, sort by size descending."""
    enriched: list[tuple[Candidate, int]] = []
    for c in candidates:
        if c.risk == RiskLevel.NEVER:
            continue
        if c.risk == RiskLevel.RISKY and not include_risky:
            continue
        size = c.size_bytes or directory_size(c.path)
        if size < min_size:
            continue
        enriched.append((c, size))
    enriched.sort(key=lambda pair: pair[1], reverse=True)
    return enriched


# --- click commands ---------------------------------------------------------


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="winspace", message="%(prog)s %(version)s")
def main() -> None:
    """winspace — clean up C: drive by relocating large directories.

    Run ``winspace scan`` to discover candidates, then ``winspace move``
    to relocate them while keeping the original paths working through
    NTFS junctions.
    """


@main.command()
@click.option("--top", default=30, type=int, show_default=True, help="Max rows to show.")
@click.option(
    "--min-size",
    "min_size_str",
    default="200MB",
    show_default=True,
    help='Skip dirs smaller than this (e.g. "500MB", "2G", "1024").',
)
@click.option(
    "--target-drive",
    "target_drive",
    type=click.Path(path_type=Path),
    default=None,
    help="Display free space on this drive in the header.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.option("--include-risky", is_flag=True, help="Include RISKY candidates.")
def scan(
    top: int,
    min_size_str: str,
    target_drive: Path | None,
    as_json: bool,
    include_risky: bool,
) -> None:
    """扫描候选目录 / Discover relocatable directories.

    Runs every registered detector and ranks results by directory size.
    """
    min_size = parse_size(min_size_str)
    fs = RealFileSystem()
    detectors = discover_detectors()
    candidates: list[Candidate] = []
    for det in detectors:
        candidates.extend(det.find(fs))

    enriched = _enrich_candidates(candidates, min_size=min_size, include_risky=include_risky)
    enriched = enriched[:top]

    if as_json:
        payload = {
            "min_size_bytes": min_size,
            "include_risky": include_risky,
            "results": [
                {
                    "path": str(c.path),
                    "category": c.category,
                    "risk": c.risk.value,
                    "size_bytes": size,
                    "reason_zh": c.reason_zh,
                    "reason_en": c.reason_en,
                    "detector": c.detector_name,
                }
                for c, size in enriched
            ],
        }
        click.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    if target_drive is not None:
        try:
            free = fs.get_free_space(target_drive)
            click.echo(f"目标盘 {target_drive}: {format_size(free)} 可用空间")
        except OSError as e:
            click.echo(f"目标盘 {target_drive} 不可用: {e}", err=True)

    if not enriched:
        click.echo(f"未找到 >= {format_size(min_size)} 的候选目录")
        click.echo("(尝试 --min-size 5MB 或 --include-risky 看更多)")
        return

    click.echo(f"找到 {len(enriched)} 个候选目录(>= {format_size(min_size)}):")
    click.echo("")
    click.echo(f"{'大小':>10}  {'风险':<8}  {'类型':<14}  路径")
    click.echo("-" * 80)
    for c, size in enriched:
        click.echo(f"{format_size(size):>10}  {c.risk.value:<8}  {c.category:<14}  {c.path}")


@main.command()
@click.argument("source", type=click.Path(path_type=Path))
@click.option(
    "--to",
    "to_drive",
    type=click.Path(path_type=Path),
    required=True,
    help="Destination drive root (e.g., D:\\).",
)
@click.option("--yes", "skip_confirm", is_flag=True, help="Skip the y/N prompt.")
@click.option("--dry-run", is_flag=True, help="Show the plan without writing anything.")
def move(source: Path, to_drive: Path, skip_confirm: bool, dry_run: bool) -> None:
    """移动目录到其他盘 / Relocate <source> to <--to> and leave a junction."""
    if not source.exists():
        click.echo(f"路径不存在 / source missing: {source}", err=True)
        sys.exit(EXIT_BAD_ARGS)

    click.echo(f"源目录 / source:      {source}")
    click.echo(f"目标盘 / dst-drive:   {to_drive}")
    click.echo(f"模式 / mode:          {'dry-run' if dry_run else 'execute'}")

    if not skip_confirm and not dry_run and not click.confirm("继续? / Proceed?", default=False):
        click.echo("已取消 / cancelled.")
        sys.exit(EXIT_USER_CANCEL)

    try:
        result = execute_move(source, to_drive, dry_run=dry_run)
    except BaseException as e:
        sys.exit(_handle_error(e))

    click.echo("")
    click.echo("完成 / done:")
    click.echo(f"  新位置 / new: {result.dst}")
    click.echo(f"  大小 / size:  {format_size(result.size_bytes)}")
    click.echo(f"  文件数:        {result.file_count}")
    if dry_run:
        click.echo("  (dry-run: 未做任何修改)")
    else:
        click.echo(f"  manifest id:   {result.entry_id}")
        if result.cleanup_pending:
            click.echo("  ⚠ cleanup_pending: 旧源目录残留, doctor 后续清理")


@main.command()
@click.argument("entry_id", required=False)
@click.option("--last", is_flag=True, help="Undo the most recent active entry.")
@click.option("--all", "all_entries", is_flag=True, help="Undo every active entry.")
@click.option("--yes", "skip_confirm", is_flag=True, help="Skip the y/N prompt.")
def undo(entry_id: str | None, last: bool, all_entries: bool, skip_confirm: bool) -> None:
    """撤销迁移 / Reverse a previous move by id / --last / --all."""
    manifest = load()
    active = [e for e in manifest.entries if e.status == EntryStatus.ACTIVE]

    targets = _resolve_undo_targets(active, entry_id, last, all_entries)
    if not targets:
        click.echo("没有可撤销的条目 / no active entries to undo.", err=True)
        sys.exit(EXIT_BAD_ARGS)

    click.echo(f"将撤销 {len(targets)} 项:")
    for e in targets:
        click.echo(f"  {e.id[:8]}…  {e.original_path}  ({format_size(e.size_bytes)})")
    if not skip_confirm and not click.confirm("继续? / Proceed?", default=False):
        click.echo("已取消 / cancelled.")
        sys.exit(EXIT_USER_CANCEL)

    for entry in targets:
        try:
            execute_undo(entry.id)
            click.echo(f"  ✓ 已还原 {entry.original_path}")
        except BaseException as e:
            click.echo(f"  ✗ 失败: {entry.original_path}: {e}", err=True)
            sys.exit(_handle_error(e))


def _resolve_undo_targets(
    active: list[ManifestEntry],
    entry_id: str | None,
    last: bool,
    all_entries: bool,
) -> list[ManifestEntry]:
    if all_entries:
        # Undo newest first so cleanup is intuitive.
        return sorted(active, key=lambda e: e.timestamp, reverse=True)
    if last:
        if not active:
            return []
        return [max(active, key=lambda e: e.timestamp)]
    if entry_id is None:
        # Show available IDs and let the user pick on the next invocation.
        return []
    for e in active:
        if e.id == entry_id or e.id.startswith(entry_id):
            return [e]
    return []


@main.command(name="list")
@click.option("--json", "as_json", is_flag=True)
def list_cmd(as_json: bool) -> None:
    """列出所有已迁移条目 / Show manifest entries and their health."""
    manifest = load()
    annotated = [(entry, _health_of(entry)) for entry in manifest.entries]

    if as_json:
        payload = {
            "entries": [
                {
                    "id": entry.id,
                    "original_path": entry.original_path,
                    "new_path": entry.new_path,
                    "size_bytes": entry.size_bytes,
                    "status": entry.status.value,
                    "cleanup_pending": entry.cleanup_pending,
                    "health": health,
                    "timestamp": entry.timestamp,
                }
                for entry, health in annotated
            ]
        }
        click.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    if not annotated:
        click.echo("manifest 为空 / no recorded moves.")
        return

    click.echo(f"{'ID':<10}  {'状态':<12}  {'健康':<10}  {'大小':>10}  原路径")
    click.echo("-" * 80)
    for entry, health in annotated:
        click.echo(
            f"{entry.id[:8]:<10}  "
            f"{entry.status.value:<12}  "
            f"{health:<10}  "
            f"{format_size(entry.size_bytes):>10}  "
            f"{entry.original_path}"
        )


def _health_of(entry: ManifestEntry) -> str:
    if entry.status != EntryStatus.ACTIVE:
        return entry.status.value
    original = Path(entry.original_path)
    new = Path(entry.new_path)
    if not is_junction(original):
        return "broken-link"
    if not new.exists():
        return "missing-target"
    return "ok"


if __name__ == "__main__":
    main()
