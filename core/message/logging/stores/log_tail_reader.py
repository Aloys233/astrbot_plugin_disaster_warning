"""
日志尾部条目读取器。

负责从原始日志文件（主文件 + 轮转备份）的尾部倒序读取最近 N 条日志条目，
供「/灾害预警日志导出」按新条目优先、字节预算受控地取数，
避免为取少量尾部条目而整读大文件。

条目切分口径与 LogSummaryService 保持一致：
以「换行 + 35 个等号 + 换行」为条目分隔符，含「🕐 日志写入时间:」的段为完整条目。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from astrbot.api import logger

# 与 log_summary_service 一致的条目分隔符与条目起始标记。
ENTRY_DELIMITER = "\n" + "=" * 35 + "\n"
ENTRY_MARK = "🕐 日志写入时间:"

# 单条目默认字符上限：防止个别超大载荷条目独占导出字节预算。
DEFAULT_MAX_ENTRY_CHARS = 3000
_TRUNCATION_SUFFIX = "\n…（本条已截断）"


@dataclass
class LogTailResult:
    """尾部条目读取结果。"""

    entries: list[str] = field(default_factory=list)  # 时间升序（旧→新）
    requested: int = 0  # 请求条数
    returned: int = 0  # 实际返回条数
    size_bytes: int = 0  # 条目拼接后的 UTF-8 字节数
    truncated_by_size: bool = False  # 是否因字节预算提前截断


class LogTailReader:
    """日志尾部条目读取器。"""

    def __init__(
        self,
        log_file_path: Path,
        max_files: int,
        *,
        chunk_size: int = 256 * 1024,
        max_scan_bytes: int = 64 * 1024 * 1024,
    ):
        # 主日志路径、轮转备份数与读取参数（单文件命名规则与 LogFileStore 一致）。
        self.log_file_path = Path(log_file_path)
        self.max_files = max_files
        self._chunk_size = chunk_size
        self._max_scan_bytes = max_scan_bytes

    def read_recent_entries(
        self,
        count: int,
        *,
        max_entry_chars: int = DEFAULT_MAX_ENTRY_CHARS,
        max_total_bytes: int,
    ) -> LogTailResult:
        """读取最近的日志条目（新条目优先，受条数与总字节预算约束）。

        Args:
            count: 请求条数。
            max_entry_chars: 单条目字符上限，超出截断并追加标记。
            max_total_bytes: 条目总字节数预算；达到预算后不再纳入更旧条目，
                仅当首条就超预算时会就地截断该条，保证至少返回 1 条。
        """
        count = max(1, int(count))
        collected: list[str] = []  # 新条目在前
        used_bytes = 0
        truncated_by_size = False

        for path in self._iter_log_files():
            if len(collected) >= count:
                break
            needed = count - len(collected)
            newest_first = self._read_tail_entries_from_file(path, needed)
            for entry in newest_first:
                trimmed = self._truncate_entry(entry, max_entry_chars)
                entry_bytes = len(trimmed.encode("utf-8"))
                if entry_bytes > max_total_bytes - used_bytes:
                    if not collected:
                        # 首条即超预算：就地按剩余预算截断，保证命令不因预算返回空。
                        trimmed = self._cut_to_byte_budget(
                            trimmed, max(0, max_total_bytes - used_bytes)
                        )
                        entry_bytes = len(trimmed.encode("utf-8"))
                        collected.append(trimmed)
                        used_bytes += entry_bytes
                    truncated_by_size = True
                    break
                collected.append(trimmed)
                used_bytes += entry_bytes
                if len(collected) >= count:
                    break
            if truncated_by_size:
                break

        # collected 为新→旧，反转为展示友好的旧→新时间升序。
        entries = list(reversed(collected))
        return LogTailResult(
            entries=entries,
            requested=count,
            returned=len(entries),
            size_bytes=used_bytes,
            truncated_by_size=truncated_by_size,
        )

    def _iter_log_files(self) -> list[Path]:
        """按新→旧顺序枚举主日志与轮转备份文件。"""
        return [self.log_file_path] + [
            self.log_file_path.with_suffix(f".log.{i}")
            for i in range(1, self.max_files + 1)
        ]

    def _read_tail_entries_from_file(self, path: Path, needed: int) -> list[str]:
        """从单个日志文件尾部倒序读取至多 needed 条完整条目（新→旧返回）。

        倒序分块读取并前置拼接，随后按条目分隔符切分；由于条目的
        「🕐 日志写入时间:」标记位于条目开头，含标记的段必然是完整条目，
        起点落在条目中间的不完整首段不含标记，会被自然过滤。
        """
        try:
            if not path.exists():
                return []
            file_size = path.stat().st_size
            if file_size <= 0:
                return []

            buffer = ""
            pos = file_size
            scanned = 0
            while True:
                chunk_len = min(self._chunk_size, pos)
                pos -= chunk_len
                scanned += chunk_len
                with open(path, "rb") as f:
                    f.seek(pos)
                    data = f.read(chunk_len)
                # 分块边界可能切在多字节字符中间，errors="replace" 只影响边界字符。
                buffer = data.decode("utf-8", errors="replace") + buffer
                segments = buffer.split(ENTRY_DELIMITER)
                complete = [s for s in segments if ENTRY_MARK in s]
                if (
                    len(complete) >= needed
                    or pos <= 0
                    or scanned >= self._max_scan_bytes
                ):
                    # complete 为文件内旧→新，取末尾 needed 条并反转为新→旧。
                    return list(reversed(complete[-needed:]))
        except OSError as e:
            logger.warning(f"[灾害预警] 读取日志尾部失败 {path}: {e}")
            return []

    @staticmethod
    def _truncate_entry(entry: str, max_entry_chars: int) -> str:
        """去除条目首尾空白并按字符上限截断。"""
        stripped = entry.strip()
        if len(stripped) <= max_entry_chars:
            return stripped
        return stripped[:max_entry_chars] + _TRUNCATION_SUFFIX

    @staticmethod
    def _cut_to_byte_budget(text: str, max_bytes: int) -> str:
        """按 UTF-8 字节预算截断文本（忽略截断产生的残缺多字节字符）。"""
        if max_bytes <= 0:
            return ""
        raw = text.encode("utf-8")
        if len(raw) <= max_bytes:
            return text
        return raw[:max_bytes].decode("utf-8", errors="ignore")


__all__ = [
    "DEFAULT_MAX_ENTRY_CHARS",
    "ENTRY_DELIMITER",
    "ENTRY_MARK",
    "LogTailReader",
    "LogTailResult",
]
