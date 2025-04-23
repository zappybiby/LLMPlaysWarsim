"""Input manager for sending automated input to the Warsim console via Win32 PostMessageW."""
from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes


# Initialize logger before any logging calls
logger = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)

# Win32 constants
VK_RETURN = 0x0D
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102


# Win32 functions and types
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = (EnumWindowsProc, wintypes.LPARAM)
user32.EnumWindows.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.IsWindowVisible.argtypes = (wintypes.HWND,)
user32.IsWindowVisible.restype = wintypes.BOOL

# PostMessageW Function Prototype
PostMessageW = user32.PostMessageW
PostMessageW.restype = wintypes.BOOL
PostMessageW.argtypes = (
    wintypes.HWND,  # hWnd
    wintypes.UINT,  # Msg
    wintypes.WPARAM,  # wParam
    wintypes.LPARAM,  # lParam
)

# Globals for Window Handling
_warsim_hwnd: wintypes.HWND | None = None
_target_pid: int | None = None

def _enum_windows_callback(hwnd: wintypes.HWND, lparam: wintypes.LPARAM) -> bool:
    """Callback for EnumWindows to find the target PID's visible window.
    
    Args:
        hwnd: Window handle
        lparam: Additional parameter
        
    Returns:
        bool: True to continue enumeration, False to stop
    """
    global _warsim_hwnd
    if not user32.IsWindowVisible(hwnd):
        return True  # Continue enumeration

    pid_ptr = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_ptr))
    if pid_ptr.value == _target_pid:
        _warsim_hwnd = hwnd
        return False  # Stop enumeration
    return True  # Continue enumeration


def initialize_input(pid: int) -> None:
    """Find and store the HWND for the target Warsim process.
    
    Args:
        pid: Process ID of the Warsim application
    """
    global _target_pid, _warsim_hwnd
    _target_pid = pid
    _warsim_hwnd = None  # Reset in case of re-initialization
    if not user32.EnumWindows(EnumWindowsProc(_enum_windows_callback), 0):
        err = ctypes.get_last_error()
        if not _warsim_hwnd:
            logger.error(
                f"Input Manager: EnumWindows failed while searching for PID {pid}. "
                f"Error code: {err}"
            )
    elif not _warsim_hwnd:
        logger.warning(
            f"Input Manager: EnumWindows completed but did not find a visible "
            f"window for PID {pid}."
        )
    else:
        pass

# --- Replace _send function with PostMessageW logic ---
def _post_message(msg: int, wparam: int, lparam: int) -> None:
    """Post a single window message to the target HWND.

    Args:
        msg: The window message identifier (e.g., WM_CHAR).
        wparam: The message's wParam value.
        lparam: The message's lParam value.

    Raises:
        RuntimeError: If HWND is not initialized.
        OSError: If PostMessageW fails.
    """
    if not _warsim_hwnd:
        logger.error("Input Manager: Cannot send input - Warsim HWND not initialized.")
        raise RuntimeError("Warsim HWND not initialized.")

    if not PostMessageW(_warsim_hwnd, msg, wparam, lparam):
        err = ctypes.get_last_error()
        logger.error(
            "Input Manager: PostMessageW failed (err=%d) for HWND %s. "
            "Msg=%#04x, wParam=%#04x, lParam=%#08x",
            err, _warsim_hwnd, msg, wparam, lparam
        )
        raise OSError(err, f"PostMessageW failed (Msg={msg:#04x}, HWND={_warsim_hwnd})")
    # This might help the target application process messages in order.
    time.sleep(0.01)


def send_key(ch: str) -> None:
    """Send a single Unicode character using WM_CHAR.

    Args:
        ch: Character to send.

    Raises:
        ValueError: If ch is not a single character.
        RuntimeError: If HWND is not initialized.
        OSError: If PostMessageW fails.
    """
    if len(ch) != 1:
        raise ValueError("send_key expects a single character.")
    char_code = ord(ch)
    # lParam for WM_CHAR: Bit 0-15: Repeat count (1)
    # Other bits (scan code, etc.) are often ignored by applications for WM_CHAR,
    # but we set repeat count to 1.
    lparam = 1
    _post_message(WM_CHAR, char_code, lparam)


def send_text(text: str, append_enter: bool = True) -> None:
    """Send a string of text using WM_CHAR, optionally followed by Enter.

    Args:
        text: Text to send.
        append_enter: Whether to append Enter key (WM_KEYDOWN/UP) at the end.

    Raises:
        RuntimeError: If HWND is not initialized.
        OSError: If PostMessageW fails.
    """
    if not _warsim_hwnd:
        logger.error("Input Manager: Cannot send text - Warsim HWND not initialized.")
        raise RuntimeError("Warsim HWND not initialized.")

    for ch in text:
        # Send each character via WM_CHAR
        char_code = ord(ch)
        lparam = 1 # Repeat count 1
        _post_message(WM_CHAR, char_code, lparam)
        # Short delay between characters might be needed for some applications
        time.sleep(0.01)

    if append_enter:
        # Send Enter key using WM_KEYDOWN and WM_KEYUP
        # lParam for WM_KEYDOWN: Repeat=1, ScanCode=0, Extended=0, PrevState=0, TransState=0 -> 0x00000001
        # lParam for WM_KEYUP:   Repeat=1, ScanCode=0, Extended=0, PrevState=1, TransState=1 -> 0xC0000001
        lparam_down = 0x00000001
        lparam_up = 0xC0000001
        _post_message(WM_KEYDOWN, VK_RETURN, lparam_down)
        time.sleep(0.01) # Small delay between down and up
        _post_message(WM_KEYUP, VK_RETURN, lparam_up)


def send_number(num: int, append_enter: bool = True) -> None:
    """Send a number as text.

    Args:
        num: Number to send.
        append_enter: Whether to append Enter key at the end.
    """
    send_text(str(num), append_enter)


def send_input(cmd: str) -> None:
    """Send a command string followed by Enter.

    Args:
        cmd: Command string to send
    """
    # This function is essentially redundant now with send_text(..., append_enter=True)
    # but kept for potential compatibility if it was used elsewhere.
    send_text(cmd, True)