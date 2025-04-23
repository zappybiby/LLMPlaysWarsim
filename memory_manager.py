"""Hierarchical memory management for game state persistence.

This module implements a memory system with SHORT (20), LONG and STRATEGIC (60) summaries.
It keeps a history of the last MAX_SHORT_SUMMARIES short summaries.
Sets self.request_save flag to True every time a long-term summary is written.
"""
from __future__ import annotations
import json
import collections # Add this import for deque
from pathlib import Path
from typing import List
import queue # Add queue import

from google import genai
from google.genai import types
import logging

logger = logging.getLogger(__name__)


class MemoryManager:
    """Manages hierarchical memory for game state with short and long-term summaries.

    This class handles storing game events and periodically generates summaries
    at different time scales to maintain meaningful game history.

    Attributes:
        SHORT_INTERVAL: Number of turns between short-term summaries.
        LONG_INTERVAL: Number of turns between long-term summaries.
        MAX_SHORT_SUMMARIES: The maximum number of short summaries to keep in history.
    """

    SHORT_INTERVAL = 20
    LONG_INTERVAL = 60
    MAX_SHORT_SUMMARIES = 5 # Keep the last 5 short summaries

    def __init__(
        self,
        client: genai.Client,
        models: List[str],
        save_name: str = "LLMSave",
        base_dir: str = "./saves",
        strategic_q: queue.Queue[str] | None = None, # Add queue parameter
    ):
        """Initialize the memory manager.

        Args:
            client: The Gemini API client.
            models: List of model names to use for generation.
            save_name: Name for the save files.
            base_dir: Base directory for save files.
            strategic_q: Optional queue to send strategic summaries to.
        """
        self.dir = Path(base_dir) / save_name
        self.dir.mkdir(parents=True, exist_ok=True)
        self.memfile = self.dir / f"{save_name}_memory.json"

        self.client = client
        self.models = models
        self._strategic_q = strategic_q # Store the queue

        # --- Load existing memory with error handling ---
        data = {}
        self.long_summary = ""
        self.strategic_summary = ""
        self.persona_statement = ""
        self.initial_narrative = ""
        if self.memfile.exists():
            try:
                data = json.loads(self.memfile.read_text(encoding="utf-8"))
                self.long_summary = data.get("long_summary", "")
                self.strategic_summary = data.get("strategic_summary", "")
                self.persona_statement = data.get("persona_statement", "")
                self.initial_narrative = data.get("initial_narrative", "")
            except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
                logger.warning(
                    "MemoryManager: Failed to load or parse memory file '%s': %s. "
                    "Proceeding with empty memory.", self.memfile, e
                )
                # Ensure summaries are empty if loading failed
                self.long_summary = ""
                self.strategic_summary = ""
                self.persona_statement = ""
                self.initial_narrative = ""
        # -----------------------------------------------

        self.buffer: List[str] = []
        # Use a deque for efficient fixed-size history
        self.short_summary_history: collections.deque[str] = collections.deque(maxlen=self.MAX_SHORT_SUMMARIES)
        self.turns: int = 0
        self.request_save: bool = False  # Flag used by agent

    def _ask(self, prompt: str, system_instruction: str) -> str:
        """Generate content using the Gemini API.

        Args:
            prompt: The user prompt to send to the model.
            system_instruction: System instructions for the model.

        Returns:
            Generated text response from the model or '(Summarization failed)'.
        """
        failure_placeholder = "(Summarization failed)"
        try:
            resp = self.client.models.generate_content(
                model=self.models[0],
                contents=[
                    types.Content(role="user", parts=[types.Part(text=system_instruction)]),
                    types.Content(role="user", parts=[types.Part(text=prompt)])
                ],
                 # Add short timeout? Optional.
                 # request_options=types.RequestOptions(timeout=60)
            )
            if resp.candidates and resp.candidates[0].content and resp.candidates[0].content.parts:
                result = resp.candidates[0].content.parts[0].text.strip()
                # Check if response is empty or just whitespace
                if not result:
                     logger.warning("Gemini summarization response was empty.")
                     return failure_placeholder
                return result
            else:
                # Log specific finish reason if available
                finish_reason = resp.candidates[0].finish_reason if resp.candidates else 'Unknown'
                safety_ratings = resp.candidates[0].safety_ratings if resp.candidates else 'N/A'
                logger.warning(
                    "Gemini summarization response was missing parts or content. Finish Reason: %s, Safety: %s",
                    finish_reason, safety_ratings
                )
                return failure_placeholder
        except Exception as e:
             logger.error("Gemini API call failed during summarization: %s", e, exc_info=True)
             return failure_placeholder

    def _save_disk(self) -> None:
        """Save the long-term and strategic summaries to disk."""
        self.memfile.write_text(
            json.dumps(
                {
                    "long_summary": self.long_summary,
                    "strategic_summary": self.strategic_summary,
                    "persona_statement": self.persona_statement,
                    "initial_narrative": self.initial_narrative
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def set_persona(self, persona: str) -> None:
        """Set the sovereign's persona statement.
        
        Args:
            persona: The persona/character statement for the sovereign.
        """
        self.persona_statement = persona
        # Save immediately to persist the persona
        self._save_disk()
        
    def set_initial_narrative(self, narrative: str) -> None:
        """Set the initial narrative for the campaign.
        
        Args:
            narrative: The initial narrative or origin story.
        """
        self.initial_narrative = narrative
        # Save immediately to persist the narrative
        self._save_disk()

    def add_event(self, desc: str) -> None:
        """Add event, process summaries, handle failures.

        Args:
            desc: Description of the event to add.
        """
        self.turns += 1
        self.buffer.append(desc)

        # --- Short Summary Generation --- 
        if self.turns % self.SHORT_INTERVAL == 0 and self.buffer: # Only run if buffer not empty
            
            # Build the system instruction with persona if available
            persona_prefix = ""
            if self.persona_statement:
                persona_prefix = f"You are the King of Aslona who has defined yourself as: {self.persona_statement} "
                
            system_instruction = (
                f"System: {persona_prefix}You are the King of a fantasy kingdom in the game Warsim. "
                "Your goal is to manage your kingdom effectively. Respond clearly and concisely, "
                "focusing on events, strategic goals, and the overall state of affairs relevant to your rule."
            )
            
            prompt = (
                "You are the King. Summarize the following sequence of recent game states "
                "and your reasoning for acting within them from your royal perspective "
                "in one concise paragraph. Focus on their immediate significance to your reign and "
                "kingdom management:\n\n" + "\n\n---\n\n".join(self.buffer)
            )
            
            # Attempt summarization
            new_short_summary = self._ask(prompt, system_instruction)
            
            # Check for successful summarization
            if new_short_summary != "(Summarization failed)":
                self.short_summary_history.append(new_short_summary) # Add to history (deque handles maxlen)
                # Clear the buffer ONLY on success
                self.buffer.clear()
            else:
                # Log failure and KEEP the buffer for the next attempt
                logger.warning("MemoryManager: Short summary generation failed. Buffer NOT cleared (size: %d). Will retry next cycle.", len(self.buffer))
        # --------------------------------
        
        # --- Long Summary Generation --- 
        # Check if LONG_INTERVAL reached AND there's at least one successful short summary in history
        if self.turns % self.LONG_INTERVAL == 0 and self.short_summary_history:
            latest_short_summary = self.short_summary_history[-1] # Get the most recent successful one
            
            # Build the system instruction with persona if available
            persona_prefix = ""
            if self.persona_statement:
                persona_prefix = f"You are the King of Aslona who has defined yourself as: {self.persona_statement} "
                
            system_instruction = (
                 f"System: {persona_prefix}You are the King of a fantasy kingdom in the game Warsim. "
                 "Your goal is to manage your kingdom effectively. Respond clearly and concisely, "
                 "focusing on events, strategic goals, and the overall state of affairs relevant to your rule."
            )
            
            # Include initial narrative in the long prompt if available
            narrative_context = ""
            if self.initial_narrative and not self.long_summary:
                narrative_context = f"Your origin story:\n{self.initial_narrative}\n\n"
                
            long_prompt = (
                f"You are the King. Update your official campaign history. "
                f"Combine the existing narrative with the recent summary.\n\n"
                f"{narrative_context}"
                f"Existing history:\n{self.long_summary or '(No prior history recorded)'}\n\n"
                f"Recent events summary:\n{latest_short_summary}\n\n" # Use latest from history
                f"Provide the updated, combined narrative from your perspective as King."
            )
            # Attempt long summary generation
            new_long_summary = self._ask(long_prompt, system_instruction)

            # Check for successful long summary generation
            if new_long_summary != "(Summarization failed)":
                self.long_summary = new_long_summary
                # --- Strategic Summary Generation (triggered after successful long summary) ---
                # Include initial narrative and persona if available
                narrative_context = ""
                if self.initial_narrative:
                    narrative_context = f"Initial Narrative: {self.initial_narrative}\n"
                persona_context = ""
                if self.persona_statement:
                     persona_context = f"Your Stated Persona: {self.persona_statement}\n"
                    
                strategic_prompt = (
                    f"You are the King. Based on the latest campaign history, provide a concise "
                    f"strategic overview. Focus on: current major goals, pressing threats, key allies/enemies, "
                    f"resource situation (if known), and potential next strategic moves. Be brief and action-oriented.\n\n"
                    f"{narrative_context}"
                    f"{persona_context}"
                    f"Latest Campaign History:\n{self.long_summary}\n\n"
                    f"Provide the strategic overview."
                )
                # Attempt strategic summary generation
                new_strategic_summary = self._ask(strategic_prompt, system_instruction)
                
                # Check for successful strategic summary generation
                if new_strategic_summary != "(Summarization failed)":
                     self.strategic_summary = new_strategic_summary
                     # Send to strategic queue if it exists
                     if self._strategic_q:
                          try:
                               self._strategic_q.put_nowait(self.strategic_summary)
                          except queue.Full:
                               logger.warning("MemoryManager: Strategic summary queue is full. Discarding update.")
                else:
                     logger.warning("MemoryManager: Strategic summary generation failed.")
                # -----------------------------------------------------------------------------
                # Save summaries after successful long (and potentially strategic) summary generation
                self._save_disk()
                self.request_save = True # Signal agent to save game state
            else:
                # Log failure for long summary
                logger.warning("MemoryManager: Long summary generation failed. Not saved.")
        elif self.turns % self.LONG_INTERVAL == 0:
             logger.warning("MemoryManager: LONG_INTERVAL reached (Turn %d), but no successful short summaries available in history. Skipping long/strategic update.", self.turns)
        # --------------------------------

    def load_for_session(self, has_save: bool) -> str:
        """Load and format memory summaries for the current game session.

        Args:
            has_save: Whether a save file exists to load from.

        Returns:
            Formatted string with campaign history and strategic overview.
        """
        if not has_save:
            return ""
        parts = []
        
        # Include persona if available
        if self.persona_statement:
            parts.append(f"Your established persona: {self.persona_statement}")
            
        # Include initial narrative if available
        if self.initial_narrative:
            parts.append(f"Your origin story: {self.initial_narrative}")
            
        if self.long_summary:
            parts.append("Campaign history:\n" + self.long_summary)
            
        if self.strategic_summary:
            parts.append("Strategic overview:\n" + self.strategic_summary)
            
        return "\n\n".join(parts)

    def get_persona(self) -> str:
        """Get the current persona statement."""
        return self.persona_statement
        
    def get_initial_narrative(self) -> str:
        """Get the initial narrative/origin story."""
        return self.initial_narrative

    def recent_short_summary(self) -> str:
        """Get the most recent short-term summary from history.

        Returns:
            The most recent short-term summary text, or empty string if none exist.
        """
        if self.short_summary_history:
            return self.short_summary_history[-1]
        else:
            return ""

    # --- New Getters for Context Assembly ---
    def get_long_summary(self) -> str:
        """Get the current long-term summary."""
        return self.long_summary

    def get_strategic_summary(self) -> str:
        """Get the current strategic summary."""
        return self.strategic_summary

    def get_recent_events(self) -> List[str]:
        """Get the list of raw events in the current buffer."""
        # Return a copy to prevent external modification
        return list(self.buffer)

    def get_short_summary_history(self) -> List[str]:
        """Get the list of recent short summaries."""
        return list(self.short_summary_history) # Return a list copy
    # --------------------------------------
