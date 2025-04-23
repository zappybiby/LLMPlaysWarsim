"""
Tkinter diagnostics GUI with three independent panes.

Top Pane: Displays LLM Reasoning / Action lines, separated by turns.
Middle Pane: Shows the latest strategic overview, with dynamic font sizing.
Bottom Pane: Captures general logs, debug information, and errors.
"""
from __future__ import annotations

import logging
import queue
import tkinter as tk
from tkinter import font as tkFont
from tkinter.scrolledtext import ScrolledText
from typing import Dict, Final, List, Optional, Tuple

logger = logging.getLogger(__name__)

# --- Constants ---

# Dark mode theme colors
DARK_BG = "#2b2b2b"
LIGHT_FG = "#dcdcdc"
DARK_FG = "#a9b7c6"
ACCENT_BLUE = "#6a87ec"
ACCENT_ORANGE = "#ff8c00"
ACCENT_YELLOW = "#ffc66d"
ACCENT_RED = "#ff6b68"
PREVIOUS_TURN_DIM = "#a0a0a0"  # For previous turn text

# Font settings
DEFAULT_FONT_FAMILY = "Segoe UI"
LLM_FONT_SIZE = 12
GENERAL_FONT_SIZE = 11
LABEL_FONT_SIZE = 10
MAX_STRATEGIC_FONT_SIZE = 14
MIN_STRATEGIC_FONT_SIZE = 8

# Placeholder text
STRATEGIC_PLACEHOLDER = "(No strategic summary available yet)"


class GuiManager:
    """
    Manages the Tkinter GUI, polling queues and updating display panes.
    Handles LLM reasoning/actions, strategic summaries, and general logs.
    """

    POLL_MS: Final[int] = 100  # Queue polling interval ms

    def __init__(
        self,
        llm_q: queue.Queue[Tuple[str, str]],
        general_q: queue.Queue[str],
        strategic_q: queue.Queue[str],
    ) -> None:
        """
        Initialize the GUI Manager.

        Args:
            llm_q: Queue for LLM reasoning/actions (type, message).
            general_q: Queue for general log messages (including TURN updates).
            strategic_q: Queue for the latest strategic summary string.
        """
        self.llm_q = llm_q
        self.general_q = general_q
        self.strategic_q = strategic_q

        self._font_cache: Dict[int, tkFont.Font] = {}
        self._last_strategic_font_size: Optional[int] = None
        self._adjust_font_timer_id = None # For debouncing font adjustments

        # State for LLM Pane Turn Display
        self.current_turn_number: int = 0
        self.current_llm_lines: List[Tuple[str, Tuple[str, ...]]] = []
        self.previous_llm_lines: List[Tuple[str, Tuple[str, ...]]] = []
        self._llm_pane_needs_redraw: bool = True
        self._pending_turn_number: int = 0

        # --- Setup Tkinter Root Window ---
        self.root = tk.Tk()
        self.root.title(f"Warsim Automation Diagnostics - Turn {self.current_turn_number}")
        self.root.configure(bg=DARK_BG)

        # Configure grid weights for proportional resizing
        self.root.grid_rowconfigure(0, weight=3)  # LLM pane
        self.root.grid_rowconfigure(1, weight=2)  # Strategic pane
        self.root.grid_rowconfigure(2, weight=3)  # General pane
        self.root.grid_columnconfigure(0, weight=1)

        # --- Setup UI Panes ---
        self._setup_llm_pane()
        self._setup_strategic_pane()
        self._setup_general_pane()

        self.root.after(self.POLL_MS, self._poll_queues)

    # --- Generic UI Creation Helper ---

    def _create_text_pane(
        self,
        parent: tk.Misc,
        row: int,
        title: str,
        font_config: tuple,
        pady_config: Tuple[int, int]
    ) -> ScrolledText:
        """Creates a standard titled ScrolledText pane."""
        frame = tk.Frame(parent, bg=DARK_BG)
        frame.grid(row=row, column=0, sticky="nsew", padx=4, pady=pady_config)
        frame.grid_rowconfigure(1, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        tk.Label(
            frame, text=title, font=(DEFAULT_FONT_FAMILY, LABEL_FONT_SIZE, "bold"),
            bg=DARK_BG, fg=LIGHT_FG
        ).grid(row=0, column=0, sticky="w")

        text_widget = ScrolledText(
            frame, state="disabled", font=font_config, wrap=tk.WORD,
            bg="#3c3f41", fg=LIGHT_FG, insertbackground=LIGHT_FG
        )
        text_widget.grid(row=1, column=0, sticky="nsew")
        return text_widget

    # --- Specific Pane Setup Helpers ---

    def _setup_llm_pane(self) -> None:
        """Creates and configures the LLM reasoning/action pane."""
        self.llm_txt = self._create_text_pane(
            parent=self.root, row=0, title="LLM Reasoning / Actions",
            font_config=(DEFAULT_FONT_FAMILY, LLM_FONT_SIZE), pady_config=(3, 1)
        )

        # Define text tags for styling LLM messages
        bold_llm_font = (DEFAULT_FONT_FAMILY, LLM_FONT_SIZE, "bold")
        self.llm_txt.tag_config("reasoning", foreground=ACCENT_BLUE)
        self.llm_txt.tag_config("action", foreground=ACCENT_ORANGE, font=bold_llm_font)
        self.llm_txt.tag_config("action_warning", foreground=ACCENT_YELLOW, font=bold_llm_font)
        self.llm_txt.tag_config("warning", foreground=ACCENT_YELLOW)
        self.llm_txt.tag_config("error", foreground=ACCENT_RED, font=bold_llm_font)
        self.llm_txt.tag_config("default", foreground=LIGHT_FG)
        self.llm_txt.tag_config("previous_turn_style", foreground=PREVIOUS_TURN_DIM)

        self._redraw_llm_pane()

    def _setup_strategic_pane(self) -> None:
        """Creates and configures the strategic overview pane."""
        self.strategic_txt = self._create_text_pane(
            parent=self.root, row=1, title="Strategic Overview",
            font_config=(DEFAULT_FONT_FAMILY, MAX_STRATEGIC_FONT_SIZE), pady_config=(1, 1)
        )
        self.strategic_txt.tag_config("default", foreground=LIGHT_FG)
        self._update_text_widget(
            self.strategic_txt, STRATEGIC_PLACEHOLDER, tags=("default",), replace=True
        )
        self.strategic_txt.bind("<Configure>", self._on_strategic_widget_configure)

    def _setup_general_pane(self) -> None:
        """Creates and configures the general logs pane."""
        self.gen_txt = self._create_text_pane(
            parent=self.root, row=2, title="General Logs",
            font_config=(DEFAULT_FONT_FAMILY, GENERAL_FONT_SIZE), pady_config=(1, 3)
        )
        self.gen_txt.tag_config("default", foreground=LIGHT_FG)

    # --- Queue Processing Logic ---

    def _poll_queues(self) -> None:
        """Periodically poll all queues and update the GUI."""
        llm_updated = self._drain_llm_queue()
        _ = self._drain_general_queue() # Processes TURN messages
        _ = self._drain_strategic_queue() # Updates strategic pane directly

        # Advance turn state if a TURN message initiated it, *before* redraw check.
        # This might set self._llm_pane_needs_redraw.
        self._advance_turn_state_if_pending()

        # Redraw LLM pane if new LLM messages arrived OR if the turn advanced
        if llm_updated or self._llm_pane_needs_redraw:
            self._redraw_llm_pane()
            self._llm_pane_needs_redraw = False # Reset flag

        self.root.after(self.POLL_MS, self._poll_queues)

    def _drain_llm_queue(self) -> bool:
        """Drain messages from the LLM queue and store them."""
        item_processed = False
        try:
            while True:
                item = self.llm_q.get_nowait()
                if isinstance(item, tuple) and len(item) == 2:
                    # Turn advancement is now handled centrally in _poll_queues

                    msg_type, msg = item
                    tag_exists = self.llm_txt.tag_cget(msg_type, "foreground") # Check tag exists
                    tags = (msg_type,) if tag_exists else ("default",)
                    self.current_llm_lines.append((msg, tags))
                    item_processed = True
                    # Redraw determination is now handled in _poll_queues
                else:
                    logger.warning(
                        "GUI: Unexpected item type '%s' in LLM queue: %s",
                        type(item), item
                    )
                    # Turn advancement is now handled centrally in _poll_queues
                    self._update_text_widget(
                        self.llm_txt, f"ERROR: Unexpected LLM Q Item: {item!s}",
                        tags=("error",)
                    )
                    item_processed = True # Indicate something happened for potential redraw
                    # Redraw determination is now handled in _poll_queues

        except queue.Empty:
            pass
        return item_processed

    def _drain_general_queue(self) -> bool:
        """Drain messages from the general queue, handling TURN updates."""
        item_processed = False
        try:
            while True:
                item = self.general_q.get_nowait()
                if isinstance(item, str):
                    if item.startswith("TURN: "):
                        try:
                            new_turn_number = int(item.split(":", 1)[1].strip())
                            # Set pending turn number only if it's an actual advance
                            if new_turn_number > self.current_turn_number:
                                self._pending_turn_number = new_turn_number
                            item_processed = True
                            continue # Consume the TURN message, don't display it
                        except (ValueError, IndexError) as e:
                            logger.warning(
                                "GUI: Could not parse turn update: '%s', Error: %s."
                                " Treating as regular message.", item, e
                            )
                            # Fall through to display below
                    # Display regular message or failed TURN message
                    self._update_text_widget(self.gen_txt, item, tags=("default",))
                    item_processed = True
                else:
                    logger.warning(
                        "GUI: Unexpected item type '%s' in General queue: %s",
                        type(item), item
                    )
                    self._update_text_widget(
                        self.gen_txt, f"ERROR: Unexpected General Q Item: {item!s}",
                        tags=("error",)
                    )
                    item_processed = True

        except queue.Empty:
            pass
        return item_processed

    def _drain_strategic_queue(self) -> bool:
        """Drain the strategic summary queue, updating with the last item."""
        latest_summary: Optional[str] = None
        found_new = False
        try:
            while True:
                latest_summary = self.strategic_q.get_nowait()
                found_new = True
        except queue.Empty:
            pass

        # Update only if a new summary was actually received
        if found_new and latest_summary is not None:
            self._update_strategic_summary(latest_summary)
            return True
        return False

    # --- State Management and Display Update Helpers ---

    def _advance_turn_state_if_pending(self) -> None:
        """Checks if a turn advance is pending and performs the state shift."""
        if self._pending_turn_number > self.current_turn_number:
            self.previous_llm_lines = self.current_llm_lines[:] # Copy current to previous
            self.current_llm_lines.clear()
            self.current_turn_number = self._pending_turn_number
            self.root.title(f"Warsim Automation Diagnostics - Turn {self.current_turn_number}")
            self._pending_turn_number = 0
            self._llm_pane_needs_redraw = True # Signal that redraw is needed

    def _redraw_llm_pane(self) -> None:
        """Clears and redraws the LLM pane with previous/current turn data."""
        widget = self.llm_txt
        widget.configure(state="normal")
        widget.delete("1.0", "end")

        # --- Insert Previous Turn (if exists) ---
        if self.current_turn_number > 0 and self.previous_llm_lines:
            prev_turn_num = self.current_turn_number - 1
            widget.insert("end", f"------ Turn {prev_turn_num} ------\n", ("previous_turn_style",))
            for msg, _tags in self.previous_llm_lines:
                widget.insert("end", msg + "\n", ("previous_turn_style",))
            widget.insert("end", "\n") # Separation line

        # --- Insert Current Turn ---
        widget.insert("end", f"------ Turn {self.current_turn_number} ------\n", ("default",))
        if self.current_llm_lines:
            for msg, tags in self.current_llm_lines:
                widget.insert("end", msg + "\n", tags)
        else:
            widget.insert("end", "(No LLM activity yet for this turn)\n", ("default",))

        widget.configure(state="disabled")
        widget.yview("end")

    def _update_strategic_summary(self, summary: str) -> None:
        """Update the strategic summary text and adjust font size."""
        widget = self.strategic_txt
        display_text = summary.strip() if summary and summary.strip() else STRATEGIC_PLACEHOLDER

        # Avoid unnecessary redraws if content hasn't actually changed
        current_content = widget.get("1.0", "end-1c")
        if display_text == current_content:
            return

        self._update_text_widget(widget, display_text, tags=("default",), replace=True)
        self._adjust_font_size()

    def _update_text_widget(
        self,
        widget: ScrolledText,
        text: str,
        tags: Tuple[str, ...] = ("default",),
        replace: bool = False
    ) -> None:
        """Helper to update text in a ScrolledText widget."""
        widget.configure(state="normal")
        if replace:
            widget.delete("1.0", "end")
            widget.insert("1.0", text, tags) # No newline when replacing content
        else:
            widget.insert("end", text + "\n", tags) # Add newline when appending
        widget.configure(state="disabled")
        if not replace:
            widget.yview("end") # Scroll down only when appending

    # --- Dynamic Font Sizing for Strategic Pane ---

    def _on_strategic_widget_configure(self, event=None) -> None:
        """Callback when strategic text widget is resized. Debounces font adjustment."""
        if self._adjust_font_timer_id:
            self.root.after_cancel(self._adjust_font_timer_id)
        # Schedule adjustment after a short delay to avoid rapid calls during resize
        self._adjust_font_timer_id = self.root.after(50, self._adjust_font_size)

    def _get_font(self, size: int) -> tkFont.Font:
        """Gets or creates a font of the specified size from cache, clamped."""
        if size not in self._font_cache:
            safe_size = max(MIN_STRATEGIC_FONT_SIZE, min(size, MAX_STRATEGIC_FONT_SIZE))
            self._font_cache[size] = tkFont.Font(family=DEFAULT_FONT_FAMILY, size=safe_size)
        return self._font_cache[size]

    def _adjust_font_size(self) -> None:
        """Adjust font size of strategic summary to fit available height via binary search."""
        widget = self.strategic_txt
        text_content = widget.get("1.0", "end-1c").strip()
        self._adjust_font_timer_id = None # Clear timer ID

        if not text_content or text_content == STRATEGIC_PLACEHOLDER:
            target_size = MAX_STRATEGIC_FONT_SIZE # Default size for placeholder
            if self._last_strategic_font_size != target_size:
                widget.configure(font=self._get_font(target_size))
                self._last_strategic_font_size = target_size
            return

        widget.update_idletasks() # Ensure geometry info is current
        available_height = widget.winfo_height()

        if available_height <= 1: # Widget not ready
            self._on_strategic_widget_configure() # Reschedule
            return

        best_fitting_size = MIN_STRATEGIC_FONT_SIZE
        low, high = MIN_STRATEGIC_FONT_SIZE, MAX_STRATEGIC_FONT_SIZE

        while low <= high:
            mid = (low + high) // 2
            test_font = self._get_font(mid)
            widget.configure(font=test_font)
            widget.update_idletasks()

            # Measure height based on the bounding box of the last character.
            # Note: May not be perfectly accurate for extreme text wrapping.
            bbox = widget.bbox("end-1c")
            if not bbox: 
                break # Measurement failed

            measured_height = bbox[1] + bbox[3]
            tolerance = 2 # Small tolerance for rendering variations

            if measured_height <= (available_height - tolerance):
                best_fitting_size = mid
                low = mid + 1 # Try larger
            else:
                high = mid - 1 # Try smaller

        # Apply the best fitting font size if it changed
        if best_fitting_size != self._last_strategic_font_size:
            widget.configure(font=self._get_font(best_fitting_size))
            self._last_strategic_font_size = best_fitting_size

    # --- Public Methods ---

    def start(self) -> None:
        """Start the Tkinter main loop."""
        logger.info("Starting GUI main loop...")
        self.root.mainloop()
        logger.info("GUI main loop finished.")