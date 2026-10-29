"""In-place terminal status board for shareadb.

The periodic status used to append one log line per interval, which buries the
``adb connect`` commands under rows of identical output. This module renders a
compact table as a *frame* and repaints it in place: before every log record
the frame is erased, the record is written, and the frame is redrawn below it,
so the shared connection commands stay visible at the bottom of the terminal.

When the stream is not an interactive TTY (output redirected to a file, CI, or
piped through ``tee``), the board disables itself and callers fall back to the
plain one-line-per-interval logging -- keeping logs grep-able and free of ANSI
escape codes.
"""
from __future__ import annotations

import logging
import os
import shutil
import threading
from typing import Callable, List, Optional, Sequence

# --------------------------------------------------------------------------- #
# Display-width aware text helpers (CJK labels are double-width)
# --------------------------------------------------------------------------- #


def _is_wide(ch: str) -> bool:
    """True if ``ch`` occupies two terminal columns (East Asian wide)."""

    cp = ord(ch)
    return (
        0x1100 <= cp <= 0x115F
        or 0x2E80 <= cp <= 0x303E
        or 0x3041 <= cp <= 0x33FF
        or 0x3400 <= cp <= 0x4DBF
        or 0x4E00 <= cp <= 0x9FFF
        or 0xA000 <= cp <= 0xA4CF
        or 0xAC00 <= cp <= 0xD7A3
        or 0xF900 <= cp <= 0xFAFF
        or 0xFE10 <= cp <= 0xFE19
        or 0xFE30 <= cp <= 0xFE6F
        or 0xFF00 <= cp <= 0xFF60
        or 0xFFE0 <= cp <= 0xFFE6
        or cp >= 0x1F300
    )


def display_width(text: str) -> int:
    """Terminal columns occupied by ``text`` (wide chars count as 2)."""

    return sum(2 if _is_wide(ch) else 1 for ch in text)


def pad_right(text: str, width: int) -> str:
    """Left-align ``text`` padded with spaces to exactly ``width`` columns."""

    return text + " " * max(0, width - display_width(text))


def fit(text: str, width: int) -> str:
    """Truncate ``text`` (with an ellipsis) so it fits in ``width`` columns."""

    if width <= 0:
        return ""
    if display_width(text) <= width:
        return text
    out: List[str] = []
    used = 0
    for ch in text:
        w = 2 if _is_wide(ch) else 1
        if used + w > width - 1:
            break
        out.append(ch)
        used += w
    return "".join(out) + "…"


# --------------------------------------------------------------------------- #
# Box-drawing frame (falls back to ASCII when the stream encoding lacks it)
# --------------------------------------------------------------------------- #

_UNICODE_FRAME = {
    "tl": "┌", "tr": "┐", "bl": "└", "br": "┘",
    "ml": "├", "mr": "┤", "h": "─", "v": "│",
}
_ASCII_FRAME = {
    "tl": "+", "tr": "+", "bl": "+", "br": "+",
    "ml": "+", "mr": "+", "h": "-", "v": "|",
}
_TRANSLATE_MAP = {
    0x2500: "-", 0x2502: "|", 0x250C: "+", 0x2510: "+",
    0x2514: "+", 0x2518: "+", 0x251C: "+", 0x2524: "+",
    0x2026: "...", 0x00B7: "-", 0x00D7: "x",
}


def _to_stream_text(text: str, encoding: str) -> str:
    """Coerce ``text`` to what ``encoding`` can actually store.

    Unicode box drawing is translated to ASCII first; anything still
    unencodable (e.g. CJK labels on a legacy codepage) degrades to ``?``
    instead of raising UnicodeEncodeError inside the logging handler.
    """

    try:
        text.encode(encoding)
        return text
    except (UnicodeEncodeError, LookupError):
        pass
    translated = text.translate(_TRANSLATE_MAP)
    try:
        translated.encode(encoding)
        return translated
    except (UnicodeEncodeError, LookupError):
        return translated.encode(encoding, errors="replace").decode(encoding)


# --------------------------------------------------------------------------- #
# Status block rendering (pure function -- easy to unit test)
# --------------------------------------------------------------------------- #

_STATE_LABELS = {"running": "运行中", "waiting": "等待中", "error": "异常"}

# Very wide terminals (200+ columns) would otherwise stretch the frame until it
# is hard to read; cap the board at 120 columns (left-aligned).
MAX_BOARD_WIDTH = 120

_OPTIONAL_TITLES: Sequence[tuple] = (
    ("客户端", 6),
    ("转发", 6),
    ("adb", 6),
)


def _state_value(status) -> str:
    return getattr(status.state, "value", str(status.state))


def _serial_width(statuses: Sequence, inner: int) -> int:
    """Serial column width: wide enough for the longest serial on screen.

    Network devices have serials like ``10.0.128.100:5555``; truncating those
    hides the very address that tells "device IP" apart from "this host's IP",
    so size the column to fit -- while reserving room for the other columns.
    """

    longest = max((display_width(s.serial) for s in statuses), default=0)
    cap = inner - (8 + 6) - 2 * 3 - 16  # 状态 + 代理 + separators + model min
    return max(10, min(max(10, longest), max(10, cap)))


def _table_columns(inner: int, serial_width: int) -> List[tuple]:
    """Pick the columns that fit ``inner``: mandatory first, then optional."""

    cols: List[tuple] = [("序列号", serial_width), ("状态", 8), ("代理", 6)]
    used = sum(w for _, w in cols) + 2 * (len(cols) - 1)
    for title, width in _OPTIONAL_TITLES:  # 客户端 / 转发 / adb
        # Add an optional column only if the model column still keeps >= 16
        # columns afterwards (16 = separator + room for names like
        # "AIVI2_ACCESS_DA"); otherwise drop the debug column instead.
        if used + width + 20 <= inner:
            cols.append((title, width))
            used += 2 + width
    model_w = inner - used - 2
    if model_w >= 8:
        cols.append(("型号", model_w))
    return cols


def _table_row(cols: Sequence[tuple], values: dict) -> str:
    return "  ".join(pad_right(fit(str(values.get(title, "")), w), w) for title, w in cols)


def render_block(
    *,
    statuses: Sequence,
    local_ips: Sequence[str],
    sync_age: Optional[float],
    now_str: str,
    width: int,
    client_counts: Optional[dict] = None,
    stale_after: Optional[float] = None,
) -> str:
    """Compose the framed status table (one ``\\n``-terminated block).

    ``statuses`` are ``DeviceStatus`` objects; ``client_counts`` maps serial to
    the number of live proxied connections; ``sync_age`` is seconds since the
    last successful ``adb devices`` listing (``None`` = never synced).
    """

    width = max(24, min(int(width), MAX_BOARD_WIDTH))
    inner = width - 4  # body between "│ " and " │"
    counts = client_counts or {}

    def wrap(body: str) -> str:
        return f"│ {pad_right(fit(body, inner), inner)} │"

    top = "┌" + "─" * (width - 2) + "┐"
    mid = "├" + "─" * (width - 2) + "┤"
    bottom = "└" + "─" * (width - 2) + "┘"

    lines: List[str] = []
    lines.append(top)

    title = "shareadb · ADB 设备共享"
    right = f"刷新 {now_str}"
    if display_width(title) + display_width(right) + 1 <= inner:
        header = pad_right(title, inner - display_width(right)) + right
    else:
        header = title
    lines.append(wrap(header))
    lines.append(mid)

    cols = _table_columns(inner, _serial_width(statuses, inner))
    lines.append(wrap(_table_row(cols, {t: t for t, _ in cols})))
    if statuses:
        for st in statuses:
            values = {
                "序列号": st.serial,
                "状态": _STATE_LABELS.get(_state_value(st), _state_value(st)),
                "代理": st.proxy_port,
                "客户端": counts.get(st.serial, 0),
                "转发": st.forward_port,
                "adb": st.tcp_port,
                "型号": st.model or st.product or "",
            }
            lines.append(wrap(_table_row(cols, values)))
        for st in statuses:
            if st.last_error:
                lines.append(wrap(f"备注: {st.serial} {_STATE_LABELS.get(_state_value(st), _state_value(st))} - {st.last_error}"))
    else:
        lines.append(wrap(" (暂无共享设备,等待设备接入...)"))

    lines.append(mid)
    lines.append(wrap("远程连接 (下面是本机 IP,在远程机器上执行):"))
    running = [st for st in statuses if _state_value(st) == "running"]
    cmds = [f"adb connect {ip}:{st.proxy_port}" for st in running for ip in local_ips]
    if cmds:
        for cmd in cmds:
            lines.append(wrap("  " + cmd))
    else:
        lines.append(wrap("  暂无可用连接 (设备未就绪)"))

    if sync_age is None:
        sync_text = "尚未执行"
    elif stale_after is not None and sync_age > stale_after:
        sync_text = f"停摆 · {sync_age:.0f}s前 (自愈可能已停止)"
    else:
        sync_text = f"正常 · {sync_age:.0f}s前"
    lines.append(wrap(f"同步: {sync_text}"))
    lines.append(wrap("管理: help 帮助 · clients 在线 · kick 踢出 · ban 封禁"))
    lines.append(bottom)
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# The board itself: in-place repaint mechanics
# --------------------------------------------------------------------------- #


def _enable_windows_vt(stream) -> bool:
    """Turn on VT/ANSI processing for a Windows console stream."""

    try:
        import ctypes
        import msvcrt

        handle = msvcrt.get_osfhandle(stream.fileno())
        mode = ctypes.c_uint()
        kernel32 = ctypes.windll.kernel32
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        return bool(
            kernel32.SetConsoleMode(handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING)
        )
    except Exception:
        return False


def ansi_supported(stream) -> bool:
    """True when ``stream`` is an interactive terminal we can repaint."""

    try:
        if not stream.isatty():
            return False
    except Exception:
        return False
    if os.name == "nt":
        return _enable_windows_vt(stream)
    return os.environ.get("TERM", "") != "dumb"


class StatusBoard:
    """Repaints a status frame in place beneath whatever gets logged.

    The board tracks how many lines it last drew so it can erase exactly that
    block (cursor-up + erase-to-end) before redrawing or before a log record
    is written. All operations are serialized under one lock so a log record
    emitted from another thread cannot interleave with a repaint.
    """

    def __init__(self, stream, render_fn: Optional[Callable[[int], str]] = None,
                 *, terminal_size_fn: Optional[Callable] = None,
                 ansi_check: Optional[Callable[[object], bool]] = None) -> None:
        self._stream = stream
        self._render_fn = render_fn
        self._terminal_size = terminal_size_fn or shutil.get_terminal_size
        self._lock = threading.Lock()
        self._height = 0
        # Capability check is injectable: the default probes TTY/TERM/Windows-VT,
        # tests force it so results do not depend on the host environment.
        self._enabled = (ansi_check or ansi_supported)(stream)
        self._encoding = getattr(stream, "encoding", None) or "utf-8"

    @property
    def enabled(self) -> bool:
        return self._enabled

    def disable(self) -> None:
        """Force line mode (e.g. --plain-status); drops any drawn frame."""

        with self._lock:
            self._enabled = False
            self._height = 0

    def set_render_fn(self, render_fn: Callable[[int], str]) -> None:
        with self._lock:
            self._render_fn = render_fn

    def _width(self) -> int:
        try:
            columns = self._terminal_size().columns
        except Exception:
            columns = 80
        return max(24, columns - 1)  # stay clear of the auto-wrap column

    def _clear_locked(self) -> None:
        if self._height <= 0:
            return
        try:
            self._stream.write(f"\033[{self._height}A\033[0J")
            self._stream.flush()
        except Exception:  # pragma: no cover - stream failures are non-fatal
            pass
        self._height = 0

    def _refresh_locked(self) -> None:
        if not self._enabled or self._render_fn is None:
            return
        try:
            text = self._render_fn(self._width())
        except Exception:  # pragma: no cover - a broken render must not break logging
            return
        text = _to_stream_text(text, self._encoding)
        if not text.endswith("\n"):
            text += "\n"
        self._clear_locked()
        try:
            self._stream.write(text)
            self._stream.flush()
            self._height = text.count("\n")
        except Exception:  # pragma: no cover - non-fatal
            self._height = 0

    def refresh(self) -> None:
        """Re-render the status frame in place (no-op when disabled)."""

        with self._lock:
            self._refresh_locked()

    def clear(self) -> None:
        with self._lock:
            self._clear_locked()

    def surround(self, fn: Callable[[], None]) -> None:
        """Erase the frame, run ``fn`` (e.g. emit one log record), repaint."""

        with self._lock:
            if not self._enabled:
                fn()
                return
            self._clear_locked()
            try:
                fn()
            finally:
                self._refresh_locked()


class StatusBoardHandler(logging.StreamHandler):
    """StreamHandler that repaints the status board around every record.

    This is what keeps the board pinned at the bottom of the terminal: any
    log line (proxy warnings, heal notices, admin commands) is written into
    the space vacated by the frame, and the frame is redrawn right after it.
    """

    def __init__(self, stream, board: StatusBoard) -> None:
        super().__init__(stream)
        self._board = board

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._board.surround(lambda: logging.StreamHandler.emit(self, record))
        except Exception:
            self.handleError(record)
