"""文件夹批量处理：递归扫描、保留层级、无覆盖编号和逐文件错误隔离。"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import stat
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from PIL import Image

from .core import RESTORE_NOTE, RESTORE_STATUS, is_image_candidate, load_image, process_compatible, restore_selected_image, save_png, scramble_image
from .errors import ImageToolError

OUTPUT_FOLDER = "confusing_images"


@dataclass(frozen=True)
class BatchPlan:
    """保存扫描快照；图片路径相对于所选根目录，不包含输出子树。"""

    source_directory: Path
    output_directory: Path
    files: tuple[Path, ...]
    scan_errors: tuple[str, ...] = ()
    linked_entries_skipped: int = 0


@dataclass
class BatchResult:
    """保存批量统计和路径映射，不记录用户口令，不生成额外日志文件。"""

    source_directory: Path
    output_directory: Path
    total: int
    outputs: list[tuple[Path, Path]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    scan_errors: tuple[str, ...] = ()
    linked_entries_skipped: int = 0
    cancelled: bool = False
    unverified_outputs: list[tuple[Path, Path]] = field(default_factory=list)
    unverified_notes: list[str] = field(default_factory=list)

    @property
    def unverified(self) -> int:
        """返回已保存但无法验证正确性的手动尝试数量。"""
        return len(self.unverified_outputs)

    @property
    def succeeded(self) -> int:
        """返回已成功写入磁盘的图片数量。"""
        return len(self.outputs)

    @property
    def failed(self) -> int:
        """返回处理或保存失败的图片数量。"""
        return len(self.errors)

    @property
    def completed(self) -> int:
        """返回已经完成尝试的数量，不把尚未处理的图片计为失败。"""
        return self.succeeded + self.failed


def _is_linklike(path: Path) -> bool:
    """识别符号链接和 Windows 目录联接，防止递归循环或越出所选目录。"""
    try:
        information = path.lstat()
    except FileNotFoundError:
        return False
    attributes = getattr(information, "st_file_attributes", 0)
    return stat.S_ISLNK(information.st_mode) or bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _report(progress: Callable | None, value: float, text: str) -> None:
    """报告批量总进度，回调不要求任何图形界面对象。"""
    if progress is not None:
        progress(value, text)


def collect_images(directory: str | Path, progress: Callable | None = None) -> BatchPlan:
    """只读扫描目录，排除根输出目录和链接，不在扫描阶段创建文件夹。"""
    root = Path(directory).resolve()
    if not root.is_dir():
        raise ImageToolError("所选路径不是可读取的文件夹。")
    output = root / OUTPUT_FOLDER
    files = []
    errors = []
    linked = 0

    def scan_error(error: OSError) -> None:
        """记录无法扫描的目录，避免悄悄丢掉该子树。"""
        errors.append(f"{error.filename or root}：{error.strerror or error}")

    for parent, directories, filenames in os.walk(root, topdown=True, followlinks=False, onerror=scan_error):
        current = Path(parent)
        retained = []
        for name in directories:
            child = current / name
            if child == output:
                continue
            try:
                if _is_linklike(child):
                    linked += 1
                    continue
            except OSError as error:
                scan_error(error)
                continue
            retained.append(name)
        directories[:] = sorted(retained, key=str.casefold)
        for name in filenames:
            path = current / name
            try:
                if _is_linklike(path):
                    linked += 1
                    continue
                if not is_image_candidate(path):
                    continue
            except OSError as error:
                scan_error(error)
                continue
            files.append(path.relative_to(root))
        _report(progress, 0.0, f"正在扫描子目录，已发现 {len(files)} 张图片")
    files.sort(key=lambda path: (str(path).casefold(), str(path)))
    return BatchPlan(root, output, tuple(files), tuple(errors), linked)


def _safe_directory(path: Path) -> None:
    """创建单层输出目录，拒绝已有文件、符号链接或 Windows 重解析点。"""
    if os.path.lexists(path) and (_is_linklike(path) or not path.is_dir()):
        raise ImageToolError(f"输出目录被文件或链接占用，未写入：{path}")
    path.mkdir(exist_ok=True)
    if _is_linklike(path) or not path.is_dir():
        raise ImageToolError(f"输出目录发生变化，未写入：{path}")


def _output_parent(output: Path, relative: Path) -> Path:
    """按原图相对层级创建安全的输出子目录。"""
    _safe_directory(output)
    current = output
    for part in relative.parent.parts:
        if part in ("", ".", ".."):
            raise ImageToolError("图片相对路径无效。")
        current = current / part
        _safe_directory(current)
    return current


def _source_path(root: Path, output: Path, relative: Path) -> Path:
    """处理前再次检查扫描快照，拒绝绝对路径、上级路径及后来替换的链接。"""
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ImageToolError("图片相对路径无效。")
    source = root
    for part in relative.parts:
        source = source / part
        if source == output or _is_linklike(source):
            raise ImageToolError("输入路径位于输出目录或已变为链接，已拒绝处理。")
    try:
        source.resolve().relative_to(root)
    except ValueError as error:
        raise ImageToolError("输入路径已越出所选目录。") from error
    return source


def _save_numbered(image: Image.Image, parent: Path, stem: str) -> Path:
    """自动编号并禁止覆盖，发生并发重名时重新选择下一个编号。"""
    number = 1
    while True:
        name = stem + ".png" if number == 1 else f"{stem}_{number}.png"
        output = parent / name
        if not os.path.lexists(output):
            try:
                return save_png(image, output, overwrite=False)
            except FileExistsError:
                # 修复检查后被其他进程创建同名文件时仍可能覆盖的问题。
                pass
        number += 1


def _process_one(
    root: Path,
    output: Path,
    relative: Path,
    password: str,
    algorithm: str,
    strength: int,
    inverse: bool,
    compatible: bool,
) -> tuple[str, Path | None, Path | None, bool, str]:
    """处理并保存单张图片，返回（状态、相对输入、相对输出、是否未校验、说明）。"""
    image = None
    transformed = None

    def report_file(value: float, text: str) -> None:
        """单张处理进度不在此线程回传，保留占位以便算法接口签名一致。"""
        return

    try:
        source = _source_path(root, output, relative)
        image = load_image(source)
        if inverse:
            transformed = restore_selected_image(image, password, algorithm=algorithm, manual=compatible, progress=report_file)
        elif compatible:
            transformed = process_compatible(image, password, algorithm, False, report_file)
        else:
            transformed = scramble_image(image, password, strength, report_file, algorithm=algorithm)
        parent = _output_parent(output, relative)
        _safe_directory(output)
        saved = _save_numbered(transformed, parent, relative.stem)
        unverified = transformed.info.get(RESTORE_STATUS) == "unverified"
        note = transformed.info.get(RESTORE_NOTE, "结果未校验") if unverified else ""
        return ("saved", relative, saved.relative_to(output), unverified, note)
    except MemoryError:
        return ("error", relative, None, False, "可用内存不足。")
    except Exception as error:
        return ("error", relative, None, False, str(error))
    finally:
        if transformed is not None:
            transformed.close()
        if image is not None:
            image.close()


def run_batch(
    plan: BatchPlan,
    password: str = "",
    *,
    algorithm: str = "tomato",
    strength: int = 2,
    inverse: bool = False,
    compatible: bool = False,
    progress: Callable | None = None,
    cancel_event: threading.Event | None = None,
    workers: int | None = None,
) -> BatchResult:
    """多线程逐张处理并自动保存，严格还原和手动尝试分别记录，失败继续。"""
    root = plan.source_directory.resolve()
    output = root / OUTPUT_FOLDER
    if not root.is_dir() or plan.output_directory != output:
        raise ImageToolError("批量目录配置无效，请重新选择文件夹。")
    if not isinstance(password, str) or len(password.encode("utf-8")) > 1024:
        raise ImageToolError("口令必须是 UTF-8 编码后不超过 1024 字节的字符串。")
    result = BatchResult(
        root, output, len(plan.files), scan_errors=plan.scan_errors,
        linked_entries_skipped=plan.linked_entries_skipped,
    )
    if not plan.files:
        return result
    if cancel_event is not None and cancel_event.is_set():
        result.cancelled = True
        return result
    _safe_directory(output)
    # 默认按 CPU 核数并行；numpy/Pillow 操作会释放 GIL，多线程可真正并发提速。
    thread_count = workers if workers is not None else max(1, min(32, (os.cpu_count() or 1)))
    pending = list(plan.files)
    completed = 0
    with ThreadPoolExecutor(max_workers=thread_count, thread_name_prefix="image-batch") as executor:
        futures = dict()
        # 先按线程数提交首批任务，取消时不再提交后续文件。
        initial = min(thread_count, len(pending))
        for index in range(initial):
            if cancel_event is not None and cancel_event.is_set():
                result.cancelled = True
                break
            relative = pending.pop(0)
            futures[executor.submit(_process_one, root, output, relative, password, algorithm, strength, inverse, compatible)] = relative
        while futures:
            if cancel_event is not None and cancel_event.is_set():
                result.cancelled = True
                # 修复停止后漏记的问题：已提交任务仍会保存或失败，必须继续收集全部结果。
            for future in as_completed(tuple(futures)):
                futures.pop(future, None)
                status, relative, saved_path, unverified, note = future.result()
                completed += 1
                if status == "saved":
                    pair = (relative, saved_path)
                    result.outputs.append(pair)
                    if unverified:
                        result.unverified_outputs.append(pair)
                        result.unverified_notes.append(f"{relative}：{note}")
                else:
                    result.errors.append(f"{relative}：{note}")
                _report(progress, completed / result.total, f"已处理 {completed}/{result.total} · 已保存 {result.succeeded}（未校验 {result.unverified}）· 失败 {result.failed}")
                if pending and (cancel_event is None or not cancel_event.is_set()):
                    next_relative = pending.pop(0)
                    futures[executor.submit(_process_one, root, output, next_relative, password, algorithm, strength, inverse, compatible)] = next_relative
                break
    if cancel_event is not None and cancel_event.is_set() and completed < len(plan.files):
        result.cancelled = True
    return result
