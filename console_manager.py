"""
Low‑level attachment and reading of the Warsim console buffer (Win32 only).

No ANSI / OSC escape codes are returned – you get exactly what the player sees.
"""
from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes
from typing import Optional

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


# Win32 helper structures
class COORD(ctypes.Structure):
    _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]


class SMALL_RECT(ctypes.Structure):
    _fields_ = [
        ("Left", wintypes.SHORT),
        ("Top", wintypes.SHORT),
        ("Right", wintypes.SHORT),
        ("Bottom", wintypes.SHORT),
    ]


class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
    _fields_ = [
        ("dwSize", COORD),
        ("dwCursorPosition", COORD),
        ("wAttributes", wintypes.WORD),
        ("srWindow", SMALL_RECT),
        ("dwMaximumWindowSize", COORD),
    ]


# Win32 prototypes
AttachConsole = kernel32.AttachConsole
AttachConsole.argtypes = [wintypes.DWORD]
AttachConsole.restype = wintypes.BOOL

FreeConsole = kernel32.FreeConsole
FreeConsole.argtypes = []
FreeConsole.restype = wintypes.BOOL

ReadConsoleOutputCharacterW = kernel32.ReadConsoleOutputCharacterW
ReadConsoleOutputCharacterW.argtypes = [
    wintypes.HANDLE,
    wintypes.LPWSTR,
    wintypes.DWORD,
    COORD,
    ctypes.POINTER(wintypes.DWORD),
]
ReadConsoleOutputCharacterW.restype = wintypes.BOOL

GetConsoleScreenBufferInfo = kernel32.GetConsoleScreenBufferInfo
GetConsoleScreenBufferInfo.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(CONSOLE_SCREEN_BUFFER_INFO),
]
GetConsoleScreenBufferInfo.restype = wintypes.BOOL

STD_OUTPUT_HANDLE = wintypes.DWORD(-11)
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

logger = logging.getLogger(__name__)


class ConsoleManager:
    """Attach to the Warsim console process and read its visible buffer."""

    def __init__(self) -> None:
        self._lock = threading.RLock()  # instead of Lock()
        self._pid: Optional[int] = None
        self._stdout = wintypes.HANDLE(INVALID_HANDLE_VALUE)

    # Attachment helpers
    def attach(self, pid: int) -> None:
        with self._lock:
            if self._pid == pid:
                return
            self.detach()  # Ensures we release any previous console
            if not AttachConsole(pid):
                err = ctypes.get_last_error()
                logger.error("Attach: AttachConsole failed with error code %d", err)
                raise OSError(err, f"AttachConsole failed (PID={pid})")
            self._pid = pid
            self._stdout = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
            if self._stdout == INVALID_HANDLE_VALUE:
                err = ctypes.get_last_error()
                logger.error("Attach: GetStdHandle failed with error code %d", err)
                # Detach console if GetStdHandle fails after successful attach
                self.detach()
                raise OSError(err, "GetStdHandle returned INVALID_HANDLE_VALUE")

    def detach(self) -> None:
        with self._lock:
            if self._pid is None:
                return
            # Store pid before clearing it for logging
            pid_to_log = self._pid
            self._pid = None
            self._stdout = wintypes.HANDLE(INVALID_HANDLE_VALUE)
            if not FreeConsole():
                err = ctypes.get_last_error()
                # Log warning instead of raising error during cleanup
                logger.warning(
                    "Detach: FreeConsole failed during detach (PID=%d, err=%d)",
                    pid_to_log, err
                )

    # Public API
    def capture_buffer(self) -> str:
        """Return the current visible text (exact screen)."""
        with self._lock:
            if self._pid is None:
                raise RuntimeError("Not attached to any console")

            csbi = CONSOLE_SCREEN_BUFFER_INFO()
            if not GetConsoleScreenBufferInfo(self._stdout, ctypes.byref(csbi)):
                err = ctypes.get_last_error()
                logger.error(
                    "Capture Buffer: GetConsoleScreenBufferInfo failed (err=%d)",
                    err
                )
                raise OSError(
                    ctypes.get_last_error(),
                    "GetConsoleScreenBufferInfo failed"
                )

            width = csbi.srWindow.Right - csbi.srWindow.Left + 1
            height = csbi.srWindow.Bottom - csbi.srWindow.Top + 1
            size = width * height

            buf = ctypes.create_unicode_buffer(size)
            read = wintypes.DWORD()
            origin = COORD(0, csbi.srWindow.Top)
            if not ReadConsoleOutputCharacterW(
                self._stdout, buf, size, origin, ctypes.byref(read)
            ):
                err = ctypes.get_last_error()
                logger.error(
                    "Capture Buffer: ReadConsoleOutputCharacterW failed (err=%d)",
                    err
                )
                raise OSError(
                    ctypes.get_last_error(),
                    "ReadConsoleOutputCharacterW failed"
                )

            view = buf[:read.value]
            return "\n".join(
                view[i:i + width].rstrip() for i in range(0, len(view), width)
            )

    # Context-manager helpers
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.detach()
        return False
