"""
运行日志收集器。

在插件初始化时向 Python 根记录器挂载一个内存缓冲 Handler，
持续记录本进程的控制台运行日志行（INFO 及以上，与控制台可见口径一致），
供「/灾害预警日志导出」读取最近 N 行并脱敏上传。

选择内存缓冲而非读取 AstrBot 日志文件的原因：
不同部署形态（Docker/裸机）与版本下日志文件位置不一致，
部分部署只输出到 stdout，没有可读文件；进程内捕获最可靠。
缓冲随进程存活，重启后从零重新累积。
"""

from __future__ import annotations

import logging
import threading
from collections import deque

# 与 AstrBot 控制台可读性对齐的行格式。
_LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 缓冲默认容量（行）与捕获级别（与控制台默认 INFO 口径一致，避免 DEBUG 噪声刷爆）。
DEFAULT_MAX_LINES = 20000
DEFAULT_CAPTURE_LEVEL = logging.INFO


class _RingBufferHandler(logging.Handler):
    """把格式化后的日志行写入有界环形缓冲。"""

    def __init__(self, buffer: deque, formatter: logging.Formatter):
        super().__init__()
        self._buffer = buffer
        self.setFormatter(formatter)

    def emit(self, record: logging.LogRecord) -> None:
        # 单条格式化失败直接吞掉，绝不影响宿主日志系统。
        try:
            line = self.format(record)
        except Exception:
            return
        if line:
            self._buffer.append(line)


class RuntimeLogCollector:
    """运行日志内存收集器（进程内单例）。"""

    def __init__(self, *, max_lines: int = DEFAULT_MAX_LINES):
        self._buffer: deque[str] = deque(maxlen=max(1, max_lines))
        self._lock = threading.Lock()
        self._handler: _RingBufferHandler | None = None

    @property
    def installed(self) -> bool:
        """是否已挂载到根记录器。"""
        return self._handler is not None

    def install(self, *, level: int = DEFAULT_CAPTURE_LEVEL) -> None:
        """挂载到根记录器（幂等：重复安装会先替换旧 Handler）。"""
        self.uninstall()
        handler = _RingBufferHandler(
            self._buffer, logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)
        )
        handler.setLevel(level)
        logging.getLogger().addHandler(handler)
        self._handler = handler

    def uninstall(self) -> None:
        """从根记录器移除并清空缓冲（幂等）。"""
        if self._handler is not None:
            try:
                logging.getLogger().removeHandler(self._handler)
            except Exception:
                pass
            self._handler = None
        with self._lock:
            self._buffer.clear()

    def get_recent_lines(
        self,
        count: int,
        *,
        keyword: str | None = None,
        max_total_bytes: int | None = None,
    ) -> tuple[list[str], bool]:
        """读取最近的日志行（时间升序返回）。

        Args:
            count: 请求行数，从最新一行往回取。
            keyword: 可选行过滤关键词（如插件日志标记 [灾害预警]），
                多行堆栈属于单条缓冲记录，会整块保留或整块丢弃。
            max_total_bytes: 可选总字节预算，达到预算后不再纳入更旧行；
                仅当首行就超预算时会就地截断该行，保证至少返回 1 行。

        Returns:
            (行列表, 是否因预算提前截断)
        """
        count = max(1, int(count))
        with self._lock:
            snapshot = list(self._buffer)

        matched = snapshot
        if keyword:
            matched = [line for line in snapshot if keyword in line]

        collected: list[str] = []  # 新行在前
        used_bytes = 0
        truncated_by_size = False
        for line in reversed(matched):
            line_bytes = len(line.encode("utf-8"))
            if max_total_bytes is not None and line_bytes > (
                max_total_bytes - used_bytes
            ):
                if not collected:
                    trimmed = self._cut_to_byte_budget(
                        line, max(0, max_total_bytes - used_bytes)
                    )
                    collected.append(trimmed)
                    used_bytes += len(trimmed.encode("utf-8"))
                truncated_by_size = True
                break
            collected.append(line)
            used_bytes += line_bytes
            if len(collected) >= count:
                break

        return list(reversed(collected)), truncated_by_size

    @staticmethod
    def _cut_to_byte_budget(text: str, max_bytes: int) -> str:
        """按 UTF-8 字节预算截断文本（忽略截断产生的残缺多字节字符）。"""
        if max_bytes <= 0:
            return ""
        raw = text.encode("utf-8")
        if len(raw) <= max_bytes:
            return text
        return raw[:max_bytes].decode("utf-8", errors="ignore")


# 进程内单例：插件初始化时安装，停机时卸载。
_runtime_log_collector: RuntimeLogCollector | None = None


def get_runtime_log_collector() -> RuntimeLogCollector:
    """获取全局运行日志收集器单例（惰性创建）。"""
    global _runtime_log_collector
    if _runtime_log_collector is None:
        _runtime_log_collector = RuntimeLogCollector()
    return _runtime_log_collector


def install_runtime_log_collector(
    *, level: int = DEFAULT_CAPTURE_LEVEL
) -> RuntimeLogCollector:
    """安装全局运行日志收集器（插件 initialize 时调用，幂等）。"""
    collector = get_runtime_log_collector()
    collector.install(level=level)
    return collector


def uninstall_runtime_log_collector() -> None:
    """卸载并清空全局运行日志收集器（插件停机时调用，幂等）。"""
    global _runtime_log_collector
    if _runtime_log_collector is not None:
        _runtime_log_collector.uninstall()


__all__ = [
    "DEFAULT_CAPTURE_LEVEL",
    "DEFAULT_MAX_LINES",
    "RuntimeLogCollector",
    "get_runtime_log_collector",
    "install_runtime_log_collector",
    "uninstall_runtime_log_collector",
]
