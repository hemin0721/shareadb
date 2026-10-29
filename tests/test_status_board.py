"""Tests for the in-place status board (repainted table, no appended lines)."""
from __future__ import annotations

import io
import logging
import os

from shareadb.device_manager import DeviceStatus, SessionState
from shareadb.status_board import (
    StatusBoard,
    StatusBoardHandler,
    ansi_supported,
    display_width,
    fit,
    pad_right,
    render_block,
)

# Force the board "enabled" in tests: the real capability probe depends on
# TERM (e.g. "dumb" here) and on being a real TTY.
_FORCE_ANSI = {"ansi_check": lambda stream: True}


class AnsiScreen:
    """Minimal ANSI terminal: interprets CSI cursor-up / erase-to-end.

    Lets tests assert what the *screen* shows after repaints, which is the only
    meaningful check for "refreshed in place instead of appended".
    """

    def __init__(self, columns: int = 80) -> None:
        self.columns = columns
        self.buf = [""]
        self.row = 0
        self.col = 0

    def write(self, s: str) -> None:
        i = 0
        while i < len(s):
            ch = s[i]
            if ch == "\x1b" and i + 1 < len(s) and s[i + 1] == "[":
                j = i + 2
                while j < len(s) and not s[j].isalpha():
                    j += 1
                if j >= len(s):
                    break
                self._csi(s[i + 2 : j], s[j])
                i = j + 1
                continue
            if ch == "\n":
                self.row += 1
                self.col = 0
                while len(self.buf) <= self.row:
                    self.buf.append("")
            elif ch == "\r":
                self.col = 0
            else:
                self._put(ch)
            i += 1

    def _csi(self, params: str, final: str) -> None:
        if final == "A":  # cursor up
            n = int(params) if params.isdigit() and params else 1
            self.row = max(0, self.row - n)
        elif final == "J" and params in ("0", ""):  # erase from cursor to end
            while len(self.buf) <= self.row:
                self.buf.append("")
            self.buf = self.buf[: self.row]
            self.buf.append("")
            self.col = 0

    def _put(self, ch: str) -> None:
        while len(self.buf) <= self.row:
            self.buf.append("")
        line = self.buf[self.row]
        if self.col > len(line):
            line = line + " " * (self.col - len(line))
        self.buf[self.row] = line[: self.col] + ch + line[self.col + 1 :]
        self.col += 1

    def screen(self) -> str:
        return "\n".join(self.buf)


class FakeTty:
    """TTY-like stream that claims to be interactive and mirrors writes into
    an AnsiScreen (io.StringIO's .encoding is read-only, hence the proxy)."""

    def __init__(self, columns: int = 80, encoding: str = "utf-8") -> None:
        self._buf = io.StringIO()
        self.columns = columns
        self.encoding = encoding
        self.ansi = AnsiScreen(columns)

    def isatty(self) -> bool:
        return True

    def write(self, s):
        self.ansi.write(s)
        return self._buf.write(s)

    def flush(self) -> None:
        self._buf.flush()

    def fileno(self):
        raise io.UnsupportedOperation("FakeTty has no file descriptor")

    def getvalue(self) -> str:
        return self._buf.getvalue()

    def terminal_size(self):
        return os.terminal_size((self.columns, 24))


def _running_status() -> DeviceStatus:
    return DeviceStatus(
        serial="bee3cd72",
        state=SessionState.RUNNING,
        forward_port=6000,
        proxy_port=7000,
        tcp_port=5555,
        model="AIVI2_ACCESS_DA",
        product="aivi2_r_da_aub",
    )


def _render(width: int, now_str: str = "10:30:05", **kw) -> str:
    kw.setdefault("statuses", [_running_status()])
    kw.setdefault("local_ips", ["10.0.28.34"])
    kw.setdefault("sync_age", 2.0)
    kw.setdefault("stale_after", 90.0)
    return render_block(now_str=now_str, width=width, **kw)


# --------------------------------------------------------------------------- #
# Text helpers
# --------------------------------------------------------------------------- #


def test_displayWidth_cjkCountsDouble() -> None:
    assert display_width("设备") == 4
    assert display_width("abc") == 3
    assert display_width("│") == 1


def test_padRight_and_fit_respectDisplayWidth() -> None:
    assert pad_right("设备", 6) == "设备  "
    assert display_width(fit("设备AIVI2_ACCESS_DA_VERY_LONG", 8)) <= 8
    assert fit("abc", 3) == "abc"


# --------------------------------------------------------------------------- #
# render_block
# --------------------------------------------------------------------------- #


def test_renderBlock_showsTableCommandsAndFitsWidth() -> None:
    # Act
    text = _render(72, client_counts={"bee3cd72": 2})

    # Assert -- table + prominent connection commands, every line in bounds
    assert "adb connect 10.0.28.34:7000" in text
    assert "bee3cd72" in text
    assert "运行中" in text
    assert "AIVI2_ACCESS_DA" in text
    assert "客户端" in text
    lines = text.splitlines()
    assert all(display_width(line) <= 72 for line in lines)
    assert lines[0].startswith("┌") and lines[0].endswith("┐")
    assert lines[-1].startswith("└") and lines[-1].endswith("┘")
    assert text.endswith("\n")


def test_renderBlock_noDevices_showsWaitingHint() -> None:
    text = _render(72, statuses=[], local_ips=[], sync_age=None)
    assert "暂无共享设备" in text
    assert "暂无可用连接" in text
    assert "尚未执行" in text
    assert "adb connect" not in text


def test_renderBlock_narrowTerminal_dropsOptionalColumnsAndStaysInBounds() -> None:
    text = _render(40)
    lines = text.splitlines()
    assert all(display_width(line) <= 40 for line in lines)
    assert "bee3cd72" in text


def test_renderBlock_syncFreshVsStale_wording() -> None:
    assert "正常" in _render(72, sync_age=3.0, stale_after=90.0)
    assert "停摆" in _render(72, sync_age=150.0, stale_after=90.0)


# --------------------------------------------------------------------------- #
# StatusBoard repaint mechanics
# --------------------------------------------------------------------------- #


def test_board_refresh_repaintsInPlace_singleFrameOnScreen() -> None:
    """Two refreshes must leave ONE frame on screen with the latest timestamp."""

    # Arrange
    tty = FakeTty(columns=80)
    state = {"now": "10:00:01"}
    board = StatusBoard(tty, terminal_size_fn=tty.terminal_size, **_FORCE_ANSI)
    board.set_render_fn(
        lambda width: _render(width, now_str=state["now"], client_counts={})
    )

    # Act
    board.refresh()
    state["now"] = "10:00:02"
    board.refresh()

    # Assert -- old frame erased, only the new one is visible, commands once
    screen = tty.ansi.screen()
    assert board.enabled is True
    assert "10:00:01" not in screen
    assert "10:00:02" in screen
    assert screen.count("adb connect 10.0.28.34:7000") == 1
    assert screen.count("shareadb · ADB 设备共享") == 1


def test_boardHandler_writesLogLineAboveRepaintedBoard() -> None:
    """Every log record lands above the board; the board stays a single frame."""

    # Arrange
    tty = FakeTty(columns=80)
    board = StatusBoard(tty, terminal_size_fn=tty.terminal_size, **_FORCE_ANSI)
    board.set_render_fn(lambda width: _render(width, client_counts={}))
    board.refresh()

    logger = logging.getLogger("shareadb.test.status_board")
    logger.handlers = []
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(StatusBoardHandler(tty, board))
    try:
        # Act
        logger.info("proxy backoff kicked in")
        logger.warning("Dead forward detected for bee3cd72; re-setting up")

        # Assert
        screen = tty.ansi.screen()
        assert "proxy backoff kicked in" in screen
        assert "Dead forward detected for bee3cd72" in screen
        assert screen.count("adb connect 10.0.28.34:7000") == 1
        assert screen.count("shareadb · ADB 设备共享") == 1
        header_at = screen.index("shareadb · ADB 设备共享")
        assert screen.index("Dead forward detected for bee3cd72") < header_at
    finally:
        logger.handlers = []


def test_ansiSupported_nonTtyStream_isFalse() -> None:
    assert ansi_supported(io.StringIO()) is False


def test_board_disabledWhenStreamIsNotATty() -> None:
    buf = io.StringIO()
    board = StatusBoard(buf, lambda width: "frame\n")
    assert board.enabled is False
    board.refresh()
    board.clear()
    assert buf.getvalue() == ""  # no ANSI garbage in redirected output


def test_board_rendersAtTerminalWidthMinusOne() -> None:
    tty = FakeTty(columns=40)
    seen: list = []
    board = StatusBoard(
        tty, lambda width: seen.append(width) or "hi\n",
        terminal_size_fn=tty.terminal_size, **_FORCE_ANSI,
    )
    board.refresh()
    assert seen == [39]  # stay clear of the auto-wrap column


def test_board_swallowsRenderErrors_soLoggingNeverBreaks() -> None:
    tty = FakeTty()
    board = StatusBoard(
        tty, lambda width: 1 / 0, terminal_size_fn=tty.terminal_size, **_FORCE_ANSI
    )
    board.refresh()  # must not raise

    logger = logging.getLogger("shareadb.test.status_board_broken")
    logger.handlers = []
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(StatusBoardHandler(tty, board))
    try:
        logger.info("still logged while board render is broken")
    finally:
        logger.handlers = []
    assert "still logged while board render is broken" in tty.ansi.screen()


# --------------------------------------------------------------------------- #
# Device-IP vs local-IP clarity (round 2: 10.0.128.x vs 10.0.138.25 confusion)
# --------------------------------------------------------------------------- #


def test_renderBlock_networkSerial_shownInFull_noTruncation() -> None:
    """A network device serial like 10.0.128.100:5555 must be fully visible.

    The serial column is what distinguishes the DEVICE address from this
    host's IP in the adb connect line; truncating it to "10.0.128..." is
    exactly what made the two addresses look like they were "alternating".
    """

    status = DeviceStatus(
        serial="10.0.128.100:5555",
        state=SessionState.RUNNING,
        forward_port=6000,
        proxy_port=7000,
        tcp_port=5555,
        model="AIVI2_ACCESS_DA",
    )
    text = render_block(
        statuses=[status],
        local_ips=["10.0.138.25"],
        sync_age=4.0,
        stale_after=90.0,
        now_str="11:29:59",
        width=72,
    )
    assert "10.0.128.100:5555" in text          # full serial, not 10.0.128.…
    assert "adb connect 10.0.138.25:7000" in text
    assert "序列号" in text
    assert all(display_width(line) <= 72 for line in text.splitlines())


def test_renderBlock_localIpSectionLabelledToAvoidConfusion() -> None:
    """The connect section must say the IP belongs to THIS host."""

    text = _render(72)
    assert "本机 IP" in text


def test_renderBlock_capsWidthOnVeryWideTerminals() -> None:
    """270-column terminals would stretch the frame unreadably; cap at 120."""

    text = _render(270)
    assert all(display_width(line) <= 120 for line in text.splitlines())
    assert max(display_width(line) for line in text.splitlines()) == 120
