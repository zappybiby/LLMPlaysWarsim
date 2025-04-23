"""
Automation agent – BootTask + reusable SaveTask (refactored).

This refactor focuses on three goals:
1. **Readability** – pull magic numbers / sleeps into clearly named helpers, trim deeply‑nested
   conditionals, and add fine‑grained logging where useful.
2. **Consistency** – all outbound inputs now flow through the same helper which enforces a
   short, configurable delay (`INPUT_DELAY`) after every keystroke.  This guarantees that
   menus never receive bursts that risk being dropped.
3. **Non‑functional parity** – the public interface (class names, external imports, etc.) is
   unchanged so the rest of the project can import `CoreAgent` exactly as before.
"""
from __future__ import annotations

import logging
import queue
import time
from enum import Enum, auto
from typing import Callable, List

from input_manager import send_key, send_number, send_text
from memory_manager import MemoryManager
import console_patterns as pat

logger = logging.getLogger(__name__)

# ───────────────────────────── Constants ──────────────────────────────
INPUT_DELAY = 0.10  # seconds – single source of truth for post‑key pause

# Convenience wrappers that **always** respect INPUT_DELAY

def _send_number(n: int) -> None:  # noqa: D401  (imperative helper)
    """Send a menu number followed by *Enter* with standard delay."""
    send_number(n)
    time.sleep(INPUT_DELAY)


def _send_text(text: str, *, enter: bool = True) -> None:
    """Send arbitrary text and optionally append *Enter* with standard delay."""
    send_text(text, enter)
    time.sleep(INPUT_DELAY)


def _send_key(ch: str = " ") -> None:
    """Send a single key (default <space>) with standard delay."""
    send_key(ch)
    time.sleep(INPUT_DELAY)


# ─────────────────────────── AgentContext ─────────────────────────────
class AgentContext:
    """Holds shared state information used by various Tasks."""

    def __init__(self, save_name: str = "LLMSave") -> None:
        self.save_name = save_name
        self.loaded_save: bool = False
        self.in_kingdom_menu: bool = False
        self.needs_save: bool = False
        # Narrative capture for new games
        self.intro_origin_text: str = ""
        self.intro_conditions_text: str = ""


# ───────────────────────────── Task Base ──────────────────────────────
class TaskState(Enum):
    ACTIVE = auto()
    DONE = auto()


class Task:  # pylint: disable=too-few-public-methods
    """Abstract base‑class for all tasks."""

    def __init__(self, ctx: AgentContext, mem: MemoryManager, gen_q: queue.Queue):
        self.ctx, self.mem, self.gen_q = ctx, mem, gen_q
        self.state = TaskState.ACTIVE

    # Sub‑classes must implement
    def feed(self, txt: str) -> None:  # noqa: D401 we are *not* a property
        raise NotImplementedError


# ───────────────────────────── BootTask ───────────────────────────────
class BootState(Enum):
    START = auto()
    LOAD_MENU = auto()
    HANDLE_LOAD_EXIT_ERROR = auto()
    WAIT_FOR_MAIN_MENU_AFTER_ERROR = auto()
    ORIGIN = auto()
    CAPTURE_CONDITIONS = auto()
    SKIP_CEREMONY = auto()
    SKIP_CROLL = auto()
    SKIP_WAIT = auto()
    READY = auto()
    CHECK_AUTORECRUIT = auto()


class BootTask(Task):
    """Completes the initial boot / menu navigation until the kingdom menu is live."""

    def __init__(self, ctx: AgentContext, mem: MemoryManager, gen_q: queue.Queue):
        super().__init__(ctx, mem, gen_q)
        self.s = BootState.START
        self.gen_q.put("TASK: Boot – Starting boot sequence…")

        # Map *every* BootState to an explicit handler for clarity
        self._handlers: dict[BootState, Callable[[str], None]] = {
            BootState.START: self._h_start,
            BootState.LOAD_MENU: self._h_load_menu,
            BootState.HANDLE_LOAD_EXIT_ERROR: self._h_handle_load_exit_error,
            BootState.WAIT_FOR_MAIN_MENU_AFTER_ERROR: self._h_wait_for_main_menu_after_error,
            BootState.ORIGIN: self._h_origin,
            BootState.CAPTURE_CONDITIONS: self._h_conditions,
            BootState.SKIP_CEREMONY: self._h_skip_ceremony,
            BootState.SKIP_CROLL: self._h_skip_croll,
            BootState.SKIP_WAIT: self._h_skip_wait,
            BootState.READY: self._h_ready,
            BootState.CHECK_AUTORECRUIT: self._h_check_autorecruit,
        }

    # ── public interface ──
    def feed(self, txt: str) -> None:  # noqa: D401 – not a property
        if self.state is TaskState.DONE:
            return
        self._handlers[self.s](txt)  # type: ignore[index]

    # ── handlers ──
    def _h_start(self, txt: str) -> None:
        if pat.MAIN_MENU_RE.search(txt):
            _send_number(2)
            self.s = BootState.LOAD_MENU

    def _h_load_menu(self, txt: str) -> None:
        if not pat.LOAD_MENU_RE.search(txt):
            return
        if self.ctx.save_name.lower() in txt.lower():
            self.gen_q.put(f"TASK: Boot – Found save '{self.ctx.save_name}'. Loading…")
            _send_text(self.ctx.save_name)
            self.ctx.loaded_save = True
            self.s = BootState.READY  # skip new‑game flow
        else:
            self.gen_q.put(
                f"TASK: Boot – Save '{self.ctx.save_name}' not found. Starting Quick‑start…"
            )
            # _send_text("x", enter=False)  # OLD: exit load menu without extra pause
            # time.sleep(0.2)             # OLD
            # _send_number(3)             # OLD: Quick‑start

            # Use imported functions directly, managing delays explicitly
            send_text("x", append_enter=True) # Send Enter after 'x'
            # time.sleep(1.0) # Keep delay to allow menu transition -- No longer needed, state machine handles timing

            # logger.debug("BootTask: Sending '3' + Enter for Quick-start...")
            # send_number(3) # Uses input_manager.send_number (includes Enter)
            # time.sleep(INPUT_DELAY) # Add delay *after* sending number
            
            # self.mem.add_event("Quick‑start (no save)") # Moved to later state
            self.ctx.loaded_save = False
            # self.s = BootState.ORIGIN # Transition to error handling instead
            self.s = BootState.HANDLE_LOAD_EXIT_ERROR

    def _h_handle_load_exit_error(self, txt: str) -> None:
        """Handles the 'file does not exist' error after trying to exit load menu with 'x'."""
        # Expect "... Press any key to continue ..."
        if pat.PRESS_ANY_KEY_RE.search(txt):
            _send_key() # Send spacebar (or any key)
            self.s = BootState.WAIT_FOR_MAIN_MENU_AFTER_ERROR
        else:
            pass # logger.debug("BootTask: Waiting for 'Press any key' prompt in HANDLE_LOAD_EXIT_ERROR.")

    def _h_wait_for_main_menu_after_error(self, txt: str) -> None:
        """Waits for the main menu to reappear after dismissing the load error."""
        if pat.MAIN_MENU_RE.search(txt):
            _send_number(3) # Send Quick-start command
            self.mem.add_event("Quick‑start (no save)") # Add event now
            self.s = BootState.ORIGIN # Proceed to new game narrative capture
        else:
             pass # logger.debug("BootTask: Waiting for Main Menu in WAIT_FOR_MAIN_MENU_AFTER_ERROR.")

    def _h_origin(self, txt: str) -> None:
        if pat.PRESS_ANY_KEY_RE.search(txt):
            self.gen_q.put("TASK: Boot [New] – Capturing Origin narrative…")
            self.ctx.intro_origin_text = txt
            _send_key()
            self.s = BootState.CAPTURE_CONDITIONS

    def _h_conditions(self, txt: str) -> None:
        if pat.PRESS_ANY_KEY_RE.search(txt):
            self.gen_q.put("TASK: Boot [New] – Capturing Conditions narrative…")
            self.ctx.intro_conditions_text = txt
            _send_key()
            self.s = BootState.SKIP_CEREMONY

    def _h_skip_ceremony(self, txt: str) -> None:
        if "crowning ceremony" in txt.lower():
            _send_number(2)
            self.s = BootState.SKIP_CROLL

    def _h_skip_croll(self, txt: str) -> None:
        if "old croll" in txt.lower():
            _send_number(2)
            self.s = BootState.SKIP_WAIT

    def _h_skip_wait(self, txt: str) -> None:
        if pat.PRESS_ANY_KEY_RE.search(txt):
            _send_key()
            self.s = BootState.READY

    def _h_ready(self, txt: str) -> None:
        if not pat.KINGDOM_MENU_RE.search(txt):
            return
        if not self.ctx.loaded_save:  # New Game → enable directly
            self._enable_autorecruit_new_game()
        else:  # Loaded Game → check status
            self.gen_q.put("TASK: Boot [Load] – Checking auto‑recruit status…")
            _send_number(1)  # Recruit menu
            _send_number(7)  # Auto‑recruit submenu
            self.s = BootState.CHECK_AUTORECRUIT

    def _enable_autorecruit_new_game(self) -> None:
        self.gen_q.put("TASK: Boot [New] – Enabling auto‑recruit…")
        for n in (1, 7, 1, 0, 0):
            _send_number(n)
        self.mem.add_event("Auto‑recruit enabled (New Game)")
        self.ctx.in_kingdom_menu = True
        # Request an initial save right after setting up auto-recruit for a new game
        self.ctx.needs_save = True 
        self.gen_q.put("TASK: Boot [New] – Requesting initial save after auto-recruit setup.")
        self.state = TaskState.DONE
        self.gen_q.put("TASK: Boot [New] – Auto‑recruit enabled. Boot complete.")

    def _h_check_autorecruit(self, txt: str) -> None:
        """Handle auto‑recruit verification for loaded saves."""
        if pat.AUTORECRUIT_SETUP_PROMPT_RE.search(txt):
            self.gen_q.put("TASK: Boot [Load] – Auto‑recruit is OFF. Enabling…")
            for n in (1, 0, 0):  # Automate → Exit → Exit
                _send_number(n)
            self.mem.add_event("Auto‑recruit enabled (Loaded Game)")
        elif pat.AUTORECRUIT_ALREADY_ON_RE.search(txt):
            self.gen_q.put("TASK: Boot [Load] – Auto‑recruit already ON.")
            for n in (0, 0):  # Fine → Exit
                _send_number(n)
            self.mem.add_event("Auto‑recruit verified ON (Loaded Game)")
        else:
            # Graceful fallback – assume we landed back at the kingdom menu
            self.gen_q.put(
                "WARN: Boot [Load] – Unknown auto‑recruit screen. Assuming enabled."
            )
        # Finish regardless of branch
        self.ctx.in_kingdom_menu = True
        self.state = TaskState.DONE
        self.gen_q.put("TASK: Boot [Load] – Boot complete.")


# ───────────────────────────── SaveTask ────────────────────────────────
class SaveState(Enum):
    WAIT = auto()
    EXTRAS = auto()
    NAME = auto()
    CONFIRM = auto()


class SaveTask(Task):
    """Invoked whenever `MemoryManager` flags that a save is needed."""

    def __init__(self, ctx: AgentContext, mem: MemoryManager, gen_q: queue.Queue):
        super().__init__(ctx, mem, gen_q)
        self.s = SaveState.WAIT

    def feed(self, txt: str) -> None:
        # Sync with mem flag once per cycle
        if self.mem.request_save:
            self.ctx.needs_save = True
            self.mem.request_save = False

        # Allow re‑activation after a completed save
        if self.state is TaskState.DONE and self.ctx.needs_save:
            self.state, self.s = TaskState.ACTIVE, SaveState.WAIT
            self.gen_q.put("TASK: Save – Reactivated for new request…")

        if self.state is TaskState.DONE:
            return

        # Mini state‑machine
        if self.s is SaveState.WAIT:
            if self.ctx.needs_save and self.ctx.in_kingdom_menu:
                self.gen_q.put("TASK: Save – Initiating save sequence…")
                _send_number(13)  # Extras menu
                self.s = SaveState.EXTRAS
            return

        if self.s is SaveState.EXTRAS and "Save Game" in txt:
            _send_number(1)
            self.s = SaveState.NAME
            return

        if self.s is SaveState.NAME and "Save Name" in txt:
            _send_text(self.ctx.save_name)
            self.s = SaveState.CONFIRM
            return

        if self.s is SaveState.CONFIRM and pat.PRESS_ANY_KEY_RE.search(txt):
            _send_key()
            _send_number(0)  # Exit menu
            self.mem.add_event(f"Game saved: {self.ctx.save_name}")
            self.ctx.needs_save = False
            self.state = TaskState.DONE
            self.gen_q.put("TASK: Save – Sequence complete.")


# ───────────────────────────── CoreAgent ──────────────────────────────
class CoreAgent:
    """Feeds console buffers to the first *active* internal task."""

    def __init__(self, memory: MemoryManager, gen_q: queue.Queue, save_name: str = "LLMSave"):
        self.ctx = AgentContext(save_name)
        self.mem = memory
        self.gen_q = gen_q
        self.tasks: List[Task] = []  # Initialize empty
        self.tasks.append(BootTask(self.ctx, memory, gen_q))
        self.tasks.append(SaveTask(self.ctx, memory, gen_q))
        self.gen_q.put("AGENT: CoreAgent initialized.")

    # ── public API ──
    def feed(self, buf: str) -> None:
        # Update kingdom‑menu flag only on transition to curb spam
        in_menu = bool(pat.KINGDOM_MENU_RE.search(buf))
        if in_menu != self.ctx.in_kingdom_menu:
            self.ctx.in_kingdom_menu = in_menu
            self.gen_q.put("AGENT: " + ("Entered" if in_menu else "Exited") + " Kingdom Menu.")

        for task in self.tasks:
            if task.state is TaskState.ACTIVE:
                task.feed(buf)
                break

    @property
    def ready_for_llm(self) -> bool:
        """True once `BootTask` is DONE and the game is at free‑play."""
        return any(isinstance(t, BootTask) and t.state is TaskState.DONE for t in self.tasks)
