"""
Tkinter diagnostics GUI with two independent panes.

Top  : LLM Reasoning / LLM Action lines
Bottom: Capture dumps, debug/info logs, errors
"""
from __future__ import annotations

import logging
import queue
import tkinter as tk
from tkinter.scrolledtext import ScrolledText
from typing import Final, Tuple, List

logger = logging.getLogger(__name__)

# Dark mode theme colors
DARK_BG = "#2b2b2b"
LIGHT_FG = "#dcdcdc"
DARK_FG = "#a9b7c6" # Used for less important text or comments if needed
ACCENT_BLUE = "#6a87ec"
ACCENT_ORANGE = "#ff8c00"
ACCENT_YELLOW = "#ffc66d"
ACCENT_RED = "#ff6b68"
PREVIOUS_TURN_DIM = "#a0a0a0" # Dimmed color for previous turn text
# Font settings
DEFAULT_FONT_FAMILY = "Segoe UI"
LLM_FONT_SIZE = 12
GENERAL_FONT_SIZE = 11
LABEL_FONT_SIZE = 10
# Placeholder text
STRATEGIC_PLACEHOLDER = "(No strategic summary available yet)"


class GuiManager:
    """Run Tkinter main‑loop and drain three queues into three panes."""

    POLL_MS: Final[int] = 100

    def __init__(
        self,
        llm_q: queue.Queue[Tuple[str, str]],
        general_q: queue.Queue[str],
        strategic_q: queue.Queue[str] # Add strategic queue
    ) -> None:
        """Initialize GUI with three queue inputs.

        Args:
            llm_q: Queue for LLM reasoning/actions (type, message)
            general_q: Queue for general log messages
            strategic_q: Queue for the latest strategic summary string
        """
        self.llm_q = llm_q
        self.general_q = general_q
        self.strategic_q = strategic_q # Store strategic queue

        # --- State for LLM Pane Turn Display ---
        self.current_turn_number: int = 0
        self.current_llm_lines: List[Tuple[str, Tuple[str, ...]]] = []
        self.previous_llm_lines: List[Tuple[str, Tuple[str, ...]]] = []
        # ---------------------------------------
        self._llm_pane_needs_redraw: bool = False # Add redraw flag
        self._pending_turn_number: int = 0  # New: track pending turn advance

        self.root = tk.Tk()
        self.root.title(f"Warsim Automation Diagnostics - Turn {self.current_turn_number}")
        self.root.configure(bg=DARK_BG) # Set root background
        # Make panes resize proportionally
        self.root.grid_rowconfigure(0, weight=3) # LLM pane (larger weight)
        self.root.grid_rowconfigure(1, weight=2) # Strategic pane
        self.root.grid_rowconfigure(2, weight=3) # General pane (larger weight)
        self.root.grid_columnconfigure(0, weight=1)

        # --- Setup UI Panes --- 
        self._setup_llm_pane()
        self._setup_strategic_pane()
        self._setup_general_pane()

        self.root.after(self.POLL_MS, self._poll_queues)

    # --- Generic UI Creation Helper ---

    def _create_text_pane(self, parent: tk.Misc, row: int, title: str, font: tuple, pady: Tuple[int, int]) -> ScrolledText:
        """Creates a standard pane containing a title label and a ScrolledText widget."""
        frame = tk.Frame(parent, bg=DARK_BG)
        frame.grid(row=row, column=0, sticky="nsew", padx=4, pady=pady)
        frame.grid_rowconfigure(1, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        tk.Label(
            frame,
            text=title,
            font=(DEFAULT_FONT_FAMILY, LABEL_FONT_SIZE, "bold"),
            bg=DARK_BG, fg=LIGHT_FG
        ).grid(row=0, column=0, sticky="w")

        text_widget = ScrolledText(
            frame,
            state="disabled",
            font=font,
            wrap=tk.WORD,
            bg="#3c3f41", fg=LIGHT_FG, insertbackground=LIGHT_FG
        )
        text_widget.grid(row=1, column=0, sticky="nsew")
        return text_widget

    # --- Specific Pane Setup Helpers (called by __init__) ---

    def _setup_llm_pane(self) -> None:
        """Creates and configures the LLM reasoning/action pane."""
        self.llm_txt = self._create_text_pane(
            parent=self.root,
            row=0,
            title="LLM Reasoning / Actions",
            font=(DEFAULT_FONT_FAMILY, LLM_FONT_SIZE),
            pady=(3, 1)
        )

        # Define text tags
        self.llm_txt.tag_config("reasoning", foreground=ACCENT_BLUE)
        self.llm_txt.tag_config("action", foreground=ACCENT_ORANGE, font=(DEFAULT_FONT_FAMILY, LLM_FONT_SIZE, "bold"))
        self.llm_txt.tag_config("action_warning", foreground=ACCENT_YELLOW, font=(DEFAULT_FONT_FAMILY, LLM_FONT_SIZE, "bold"))
        self.llm_txt.tag_config("warning", foreground=ACCENT_YELLOW)
        self.llm_txt.tag_config("error", foreground=ACCENT_RED, font=(DEFAULT_FONT_FAMILY, LLM_FONT_SIZE, "bold"))
        self.llm_txt.tag_config("default", foreground=LIGHT_FG)
        self.llm_txt.tag_config("previous_turn_style", foreground=PREVIOUS_TURN_DIM)

        # Initial content
        self._redraw_llm_pane()

    def _setup_strategic_pane(self) -> None:
        """Creates and configures the strategic overview pane."""
        self.strategic_txt = self._create_text_pane(
            parent=self.root,
            row=1,
            title="Strategic Overview",
            font=(DEFAULT_FONT_FAMILY, GENERAL_FONT_SIZE), # Use fixed general font size
            pady=(1, 1)
        )

        # Define default tag
        self.strategic_txt.tag_config("default", foreground=LIGHT_FG)

        # Set initial placeholder text
        self.strategic_txt.configure(state="normal")
        self.strategic_txt.insert("1.0", STRATEGIC_PLACEHOLDER, ("default",))
        self.strategic_txt.configure(state="disabled")

    def _setup_general_pane(self) -> None:
        """Creates and configures the general logs pane."""
        self.gen_txt = self._create_text_pane(
            parent=self.root,
            row=2,
            title="General Logs",
            font=(DEFAULT_FONT_FAMILY, GENERAL_FONT_SIZE),
            pady=(1, 3)
        )

        # Define default tag
        self.gen_txt.tag_config("default", foreground=LIGHT_FG)

    # --- Queue Processing --- 

    def _poll_queues(self) -> None:
        """Poll all queues and schedule next polling."""
        # Reset flag before draining - might be set by new messages
        self._llm_pane_needs_redraw = False

        # --- Drain queues ---
        # State shift logic is now inside _drain for llm_q
        self._drain(self.llm_q, self.llm_txt)
        self._drain(self.general_q, self.gen_txt) # Reads TURN msg, sets _pending_turn_number
        self._drain_strategic_queue()

        # Redraw LLM pane if needed (flag set by _drain if messages arrived or state shifted)
        if self._llm_pane_needs_redraw:
             self._redraw_llm_pane()

        # ---- Pending turn advance logic moved ----

        self.root.after(self.POLL_MS, self._poll_queues)

    def _drain(
        self,
        q: queue.Queue[str] | queue.Queue[Tuple[str, str]],
        widget: ScrolledText
    ) -> None:
        """Drain messages from queue and display in widget. Handles turn logic for LLM pane."""
        is_llm_widget = (widget == self.llm_txt)
        is_gen_widget = (widget == self.gen_txt)

        try:
            while True:
                item = q.get_nowait()
                # --- Centralized TURN Message Handling (only for general queue) ---
                if is_gen_widget and isinstance(item, str) and item.startswith("TURN: "):
                    try:
                        new_turn_number = int(item.split(":", 1)[1].strip())
                        # Only set pending flag if it's actually a new turn number
                        if new_turn_number > self.current_turn_number:
                             self._pending_turn_number = new_turn_number
                        continue # Consume the TURN message and get next item
                    except (ValueError, IndexError) as e:
                        logger.warning("GUI: Could not parse turn update message: '%s', Error: %s. Treating as regular message.", item, e)
                        # Fall through to process as a regular message if parsing failed

                # --- Dispatch to Specific Handlers --- 
                # --- Process LLM messages --- 
                if is_llm_widget and isinstance(item, tuple) and len(item) == 2:
                    self._handle_llm_message(item, widget)
                # --- Process General Log messages (that are NOT the TURN message) --- 
                elif is_gen_widget and isinstance(item, str):
                    self._handle_general_message(item, widget)
                # --- Handle unexpected item types --- 
                else:
                    # Handle items that don't match expected types for either queue
                    if is_llm_widget: # Unexpected item for LLM pane
                        logger.warning("GUI: Received unexpected item type '%s' for LLM pane: %s", type(item), item)
                        text_to_store = str(item)
                        # Attempt to advance turn state before adding error msg
                        self._advance_turn_state_if_pending()
                        # this bypasses the turn-based storage in current_llm_lines
                        self._update_text_widget(widget, f"ERROR: Unexpected LLM Queue Item: {text_to_store}", tags=("error",))
                    elif is_gen_widget: # Unexpected item for General pane
                         logger.warning("GUI: Received unexpected item type '%s' for General pane: %s", type(item), item)
                         self._update_text_widget(widget, str(item), tags=("error",))
                    else: # Should not happen
                         logger.error("GUI: Item '%s' received for unknown widget '%s'", item, widget.winfo_name())

        except queue.Empty:
            pass # No messages in the queue

    # --- Message Handling Helpers (called by _drain) --- 

    def _advance_turn_state_if_pending(self) -> None:
        """Checks if a turn advance is pending and performs the state shift."""
        if self._pending_turn_number > self.current_turn_number:
             # Perform LLM state shift
            self.previous_llm_lines = self.current_llm_lines[:] # Make a copy
            self.current_llm_lines.clear()
            self.current_turn_number = self._pending_turn_number
            self._pending_turn_number = 0 # Reset pending flag
            # Update title now that state has advanced
            self.root.title(f"Warsim Automation Diagnostics - Turn {self.current_turn_number}")
            self._llm_pane_needs_redraw = True # Set flag: redraw needed after shift

    def _handle_llm_message(self, item: Tuple[str, str], widget: ScrolledText) -> None:
        """Processes a message from the LLM queue."""
        # Advance turn state *before* adding the new message if pending
        self._advance_turn_state_if_pending()

        # Add message to current turn's data
        msg_type, msg = item
        # Check if tag exists before using it
        tag_exists = widget.tag_cget(msg_type, "foreground")
        tags = (msg_type,) if tag_exists else ("default",)

        self.current_llm_lines.append((msg, tags))
        self._llm_pane_needs_redraw = True # Set flag: redraw needed

    def _handle_general_message(self, item: str, widget: ScrolledText) -> None:
        """Processes a message from the general queue, handling TURN updates."""
        # NOTE: TURN message check is now handled in _drain.
        self._update_text_widget(widget, item)

    # --- LLM Pane Redrawing --- 
    def _redraw_llm_pane(self) -> None:
        """Clears and redraws the LLM pane with previous (dimmed) and current turn data."""
        self.llm_txt.configure(state="normal")
        self.llm_txt.delete("1.0", "end") # Clear the entire widget

        # --- Insert Previous Turn ---
        if self.current_turn_number > 0 and self.previous_llm_lines:
            prev_turn_num = self.current_turn_number - 1
            # Insert header for previous turn, dimmed
            self.llm_txt.insert("end", f"------ Turn {prev_turn_num} ------\n", ("previous_turn_style",))
            # Insert previous turn lines, all using the dimmed style
            for msg, _original_tags in self.previous_llm_lines:
                 self.llm_txt.insert("end", msg + "\n", ("previous_turn_style",))
            self.llm_txt.insert("end", "\n") # Add a blank line separation

        # --- Insert Current Turn ---
        # Insert header for current turn, default style
        self.llm_txt.insert("end", f"------ Turn {self.current_turn_number} ------\n", ("default",))
        # Insert current turn lines using their original semantic tags
        if self.current_llm_lines:
            for msg, original_tags in self.current_llm_lines:
                self.llm_txt.insert("end", msg + "\n", original_tags)
        else:
             # Optionally, add a placeholder if no messages yet for the current turn
             self.llm_txt.insert("end", "(No LLM activity yet for this turn)\n", ("default",))

        # Make sure font is reset to default after potential error messages
        self.llm_txt.configure(font=(DEFAULT_FONT_FAMILY, 12))
        self.llm_txt.configure(state="disabled")
        self.llm_txt.yview("end") # Ensure scrolled to the bottom

    def _drain_strategic_queue(self) -> None:
        """Drain the strategic summary queue and update the middle panel."""
        latest_summary: str | None = None
        found_new = False
        try:
            while True:
                latest_summary = self.strategic_q.get_nowait()
                found_new = True # Track if we actually got something
        except queue.Empty:
            pass # No new summaries

        # Update only if we actually received a summary from the queue this cycle
        if found_new and latest_summary is not None:
             # Simply update with the latest summary
             self._update_strategic_summary(latest_summary)

    def _update_strategic_summary(self, summary: str) -> None:
        """Update the strategic summary text."""
        widget = self.strategic_txt
        # Use placeholder if summary is empty or whitespace
        display_text = summary.strip() if summary and summary.strip() else STRATEGIC_PLACEHOLDER

        # Simply update the text widget - no content comparison needed
        self._update_text_widget(widget, display_text, replace=True)

    def start(self) -> None:
        """Start the Tkinter main loop."""
        self.root.mainloop()

    def _update_text_widget(self, widget: ScrolledText, text: str, tags: Tuple[str, ...] = (), replace: bool = False) -> None:
        """Updates the text in a ScrolledText widget with the specified text and tags."""
        widget.configure(state="normal")
        if replace:
            widget.delete("1.0", "end")
        widget.insert("end", text + "\n", tags)
        widget.configure(state="disabled")
        widget.yview("end")
