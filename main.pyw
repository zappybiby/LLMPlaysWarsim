#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLMPlaysWarsim - AI agent that plays the text-based kingdom management game Warsim.

This module serves as the entry point for the LLMPlaysWarsim application. It manages
the connection to Warsim's console, initializes the LLM-powered agent, and
coordinates communication between components through a background thread and GUI.

Key functionality:
- Attaches to Warsim's console once on the main thread
- Sets up logging and configuration
- Initializes the Gemini AI model
- Manages game state through memory and agent components
- Handles threads for game monitoring and LLM interaction
- Provides a GUI for monitoring system activity
"""

from __future__ import annotations

# Standard library imports
import logging
import os
import queue
import threading
import time
from typing import Optional, Tuple

# Third-party imports
import psutil
from dotenv import load_dotenv
from google import genai

# Local application imports
from console_manager import ConsoleManager
from core_agent import CoreAgent
from gui_manager import GuiManager
import input_manager
from llm_manager import LLMManager
from memory_manager import MemoryManager

# Configuration constants
SAVE_NAME = "LLMSave"
MODEL_NAME = "gemini-2.0-flash"
CONSOLE_POLL_S = 0.30

# Game advice and tips for new games
NEW_GAME_TIPS = (
    "Note: Auto‑recruit is enabled and automatically handles buying/selling of troops. "
    "Do NOT disable it.\n\n"
    "Advice for early turns:\n"
    "- Spend first days in the Throne Room to gather quests.\n"
    "- Explore nearby lands early.\n"
    "- Use arena betting if in need of gold.\n"
    "- Hire Mercenary Companies once affordable.\n"
    "- Before combat, inspect enemy troop counts with hired diplomat.\n"
    "- Hire a Diplomat to unlock diplomacy options."
)

# Set up logging configuration
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    filename="debug.log",
    filemode="w",  # overwrite on each run
)
logger = logging.getLogger(__name__)


def setup_gemini_client():
    """
    Initialize the Gemini AI client with API key from environment.
    
    Returns:
        genai.Client: Configured Gemini client instance
        
    Raises:
        RuntimeError: If API key is not set in environment
    """
    load_dotenv()
    api_key = os.getenv("GOOGLE_API_KEY")
    
    if not api_key:
        logger.error("GOOGLE_API_KEY not found in environment variables or .env file.")
        raise RuntimeError("GOOGLE_API_KEY not set – put it in .env or env var")
    
    return genai.Client(api_key=api_key)


def find_warsim_pid() -> int:
    """
    Find the process ID of the running Warsim.exe process.
    
    Returns:
        int: Process ID of Warsim
        
    Raises:
        RuntimeError: If Warsim process is not found
    """
    for proc in psutil.process_iter(["pid", "name"]):
        if (name := proc.info.get("name")) and name.lower() == "warsim.exe":
            return proc.info["pid"]
    raise RuntimeError("Could not locate Warsim.exe. Make sure the game is running.")


def _add_and_log(desc: str):
    """
    Add an event to memory and log it to the general queue.
    
    Args:
        desc (str): Event description to add and log
    """
    _orig_add(desc)
    # Send a generic confirmation instead of the full description
    # Determine if it's the initial narrative or a regular event
    if desc.startswith("New Game Narrative:"):
        log_msg = "MEM: Initial narrative added to memory buffer."
    else:
        log_msg = "MEM: Game state/reasoning event added to memory buffer."
    general_q.put(log_msg)


def runner(console: ConsoleManager) -> None:
    """
    Capture Warsim buffer, assemble context, drive agent/LLM interactions,
    and feed game state + LLM reasoning into MemoryManager.
    
    This function runs in a background thread and handles the main loop.
    
    Args:
        console (ConsoleManager): Pre-attached console manager instance
    """
    llm_initial_context_loaded = False  # Track if initial memory/tips are loaded
    last_event_added_to_memory: Optional[str] = None # Track last full event string added
    last_buffer_for_llm: Optional[str] = None # Track buffer *sent* to LLM for event logging

    loop_count = 0
    try:
        while True:
            loop_count += 1
            
            # --- Capture current screen --- 
            # We capture *first* to get the absolute latest state for the LLM decision
            current_buf = console.capture_buffer()
            if not current_buf:
                time.sleep(CONSOLE_POLL_S)
                continue

            # --- Feed Agent (Handles Boot/Save Tasks) --- 
            agent.feed(current_buf) # Agent might change game state based on current_buf

            # --- Propagate memory's save request --- 
            if memory.request_save:
                agent.ctx.needs_save = True
                memory.request_save = False

            # --- LLM Turn Logic --- 
            ready = agent.ready_for_llm
            
            if ready:  # Agent boot sequence is complete
                # --- Initial Context Loading (Once) --- 
                if not llm_initial_context_loaded:
                    
                    # --- Context Logic: Loaded Save vs New Game ---
                    if agent.ctx.loaded_save:
                        # Load existing memory if a save was loaded
                        prior_memory = memory.load_for_session(agent.ctx.loaded_save)
                        if prior_memory:
                            memory_prompt = (
                                f"System: Resuming campaign. Previous context:\n\n{prior_memory}"
                            )
                            llm._add_to_history(role="user", parts=[memory_prompt])
                            log_msg = "Loaded memory summaries into LLM context."
                            general_q.put(log_msg)
                        else:
                            # Case where save exists but memory file doesn't (or is empty/corrupt)
                            log_msg = "Save loaded, but no prior memory context found/added."
                            general_q.put(log_msg)
                    else: # New Game
                        # Combine captured narrative and new game tips
                        if agent.ctx.intro_origin_text or agent.ctx.intro_conditions_text:
                            intro_narrative = (
                                f"{agent.ctx.intro_origin_text.strip()}\n\n" 
                                f"{agent.ctx.intro_conditions_text.strip()}"
                            ).strip()
                            combined_prompt = (
                                f"System: Starting a new campaign. Initial Narrative:\n\n"
                                f"{intro_narrative}\n\n"
                                f"Tips for your reign:\n\n{NEW_GAME_TIPS}"
                            )
                            # Add combined context to LLM
                            llm._add_to_history(role="user", parts=[combined_prompt])

                            # --- Add Personality Choice Prompt ---
                            personality_prompt = (
                                "System: Before thy first decree, declare the nature of thy rule. Art thou a benevolent unifier, "\
                                "a cunning strategist, a ruthless tyrant, or something else entirely? State thy chosen persona "\
                                "clearly within thy reasoning for the first action."
                            )
                            llm._add_to_history(role="user", parts=[personality_prompt])
                            # -------------------------------------

                            # Add *only* the narrative part to MemoryManager buffer
                            memory.add_event(f"New Game Narrative:\n{intro_narrative}") 
                            log_msg = "Injected captured narrative and new-game tips into LLM context."
                            general_q.put(log_msg)
                            log_msg_mem = "Added captured narrative to MemoryManager buffer."
                            general_q.put(f"MEM: {log_msg_mem}") # Use MEM prefix like _add_and_log
                        else:
                            # Fallback if narrative capture somehow failed, just add tips
                            logger.warning("Runner: New game started, but intro narrative was not captured by BootTask. Adding only tips.")
                            tips_prompt = f"System: Starting a new campaign. Tips:\n\n{NEW_GAME_TIPS}"
                            llm._add_to_history(role="user", parts=[tips_prompt])
                            # --- Add Personality Choice Prompt (Fallback) ---
                            personality_prompt = (
                                "System: Before thy first decree, declare the nature of thy rule. Art thou a benevolent unifier, "\
                                "a cunning strategist, a ruthless tyrant, or something else entirely? State thy chosen persona "\
                                "clearly within thy reasoning for the first action."
                            )
                            llm._add_to_history(role="user", parts=[personality_prompt])
                            # ----------------------------------------------
                            log_msg = "Injected new-game advisory tips for LLM (narrative capture failed)."
                            general_q.put(log_msg)
                    # --------------------------------------------------

                    # Update token count *after* adding initial context
                    llm._update_token_count()
                    llm_initial_context_loaded = True  # Mark initial context as loaded

                # --- Assemble Context for LLM --- 
                long_summary = memory.get_long_summary()
                strategic_summary = memory.get_strategic_summary()
                # Fetch the history of short summaries
                short_summaries = memory.get_short_summary_history()
                recent_events = memory.get_recent_events() # MemoryManager.buffer
                
                context_parts = []
                # System Prompt is handled by llm.step config

                # Order: Long -> Strategic -> Short History -> Recent Raw -> Current Screen
                if long_summary:
                    context_parts.append(f"Campaign History Summary:\n{long_summary.strip()}")
                if strategic_summary:
                    context_parts.append(f"Strategic Overview:\n{strategic_summary.strip()}")
                
                # Add the history of short summaries
                if short_summaries:
                    context_parts.append("Recent Short Summaries (Newest Last):")
                    context_parts.append("--- START SHORT SUMMARIES ---")
                    # Add summaries numbered for clarity
                    for i, summary in enumerate(short_summaries):
                         context_parts.append(f"Summary {i+1}:\n{summary.strip()}")
                    context_parts.append("--- END SHORT SUMMARIES ---")
                
                # Add recent raw events
                if recent_events:
                    context_parts.append("Recent Events Log (State + Reasoning):")
                    context_parts.append("--- START RECENT EVENTS ---")
                    context_parts.extend(recent_events) # Add individual raw events
                    context_parts.append("--- END RECENT EVENTS ---")
                
                # Add the current screen buffer last
                context_parts.append("Current Game Screen:")
                context_parts.append(current_buf.strip())
                
                assembled_context = "\n\n".join(context_parts)
                # --------------------------------
                
                last_buffer_for_llm = current_buf # Store buffer *sent* to LLM for memory add later
                llm.step(assembled_context) # Pass assembled context

                # --- Capture Initial Personality (if first turn of new game) ---
                if not agent.ctx.loaded_save and llm._turn == 1 and llm.last_reasoning:
                    initial_persona_reasoning = llm.last_reasoning
                    general_q.put(f"SYS: Initial persona declared: {initial_persona_reasoning}")
                    # Store the initial persona in MemoryManager
                    memory.set_persona(initial_persona_reasoning)
                    
                    # Also store the initial narrative if we have it
                    if agent.ctx.intro_origin_text or agent.ctx.intro_conditions_text:
                        intro_narrative = (
                            f"{agent.ctx.intro_origin_text.strip()}\n\n" 
                            f"{agent.ctx.intro_conditions_text.strip()}"
                        ).strip()
                        memory.set_initial_narrative(intro_narrative)
                # -------------------------------------------------------------

                # --- Add Game State + Reasoning to Memory (After LLM step, if action taken) --- 
                if llm.action_taken_this_turn: # Check the flag from LLMManager
                    current_reasoning = llm.last_reasoning # Get reasoning from LLMManager
                    
                    if last_buffer_for_llm: # Ensure we have the buffer that the LLM used
                        reasoning_text = current_reasoning if current_reasoning else "(No reasoning retrieved)"
                        
                        # Format the event string using the buffer *sent* to the LLM
                        memory_event = (
                            f"Game state:\n{last_buffer_for_llm.strip()}\n\n"
                            f"Reasoning:\n{reasoning_text.strip()}"
                        )
                        
                        # Avoid adding the exact same event string consecutively
                        if memory_event != last_event_added_to_memory:
                            # Add the combined event to memory. 
                            # MemoryManager handles turn count and summary triggers.
                            memory.add_event(memory_event) # _add_and_log wrapper logs to general_q
                            last_event_added_to_memory = memory_event # Update last added event
                    else:
                        logger.warning("Runner: Cannot add event to memory - buffer sent to LLM was None.")
                # --- End Memory Add --- 
            else:
                pass # Added pass to fix indentation

            time.sleep(CONSOLE_POLL_S)

    except Exception as exc:
        logger.exception("Error in runner thread")
        general_q.put(f"FATAL ERROR in runner: {exc}")


if __name__ == "__main__":
    # Initialize Gemini client
    client = setup_gemini_client()
    
    # Create communication queues
    llm_q: queue.Queue[Tuple[str, str]] = queue.Queue() # Explicitly type hint
    general_q: queue.Queue[str] = queue.Queue()
    strategic_q: queue.Queue[str] = queue.Queue() # Add strategic queue

    # --- Initial Setup Logging (Send to General Queue) ---
    general_q.put("SYS: Initializing...") # Add initial message
    try:
        # Log API key status to queue
        api_key = os.getenv("GOOGLE_API_KEY") # Re-check for masked logging
        if api_key:
             masked_key = f"{api_key[:2]}...{api_key[-2:]}" if len(api_key) > 9 else "<key too short>"
             general_q.put(f"SYS: Google API Key loaded (masked: {masked_key})")
        else:
             general_q.put("ERR: Google API Key not found!") # Should have been caught by setup_gemini
    except Exception as e:
         general_q.put(f"ERR: Could not verify API key for logging: {e}")
    # ---------------------------------------------------

    # Initialize core components
    memory = MemoryManager(client=client, models=[MODEL_NAME], save_name=SAVE_NAME, strategic_q=strategic_q)
    general_q.put("SYS: Memory Manager initialized.")
    agent = CoreAgent(memory, gen_q=general_q, save_name=SAVE_NAME)
    general_q.put("SYS: Core Agent initialized.")
    llm = LLMManager(
        client=client,
        model=MODEL_NAME, 
        llm_q=llm_q, 
        general_q=general_q
    )
    
    # Set up memory event logging
    _orig_add = memory.add_event
    memory.add_event = _add_and_log  # monkey-patch

    try:
        general_q.put("SYS: Finding Warsim process...") # Add queue message
        warsim_pid = find_warsim_pid()
        general_q.put(f"SYS: Warsim PID found: {warsim_pid}") # Add queue message
    except Exception as exc:
        logger.error(f"Failed to find Warsim PID: {exc}")
        general_q.put(f"ERR: Failed to find Warsim PID: {exc}") # Add error to queue
        # Optional: Display error in a simple Tkinter message box before exiting?
        raise SystemExit(1)

    # Attach to console on main thread
    try:
        general_q.put("SYS: Attaching to Warsim console...") # Add queue message
        main_console = ConsoleManager()
        main_console.attach(warsim_pid)
        general_q.put("SYS: Attached to console.") # Add queue message

        # Initialize input manager with PID
        general_q.put("SYS: Initializing input manager...") # Add queue message
        input_manager.initialize_input(warsim_pid)
        general_q.put("SYS: Input manager initialized.") # Add queue message
    except Exception as exc:
        logger.error(f"Failed to attach console or initialize input: {exc}")
        general_q.put(f"ERR: Failed to attach console or initialize input: {exc}") # Add error to queue
        # Optional: Display error in a simple Tkinter message box before exiting?
        raise SystemExit(1)

    # Start background processing thread
    general_q.put("SYS: Starting runner thread...") # Add queue message
    threading.Thread(
        target=runner, 
        args=(main_console,), 
        daemon=True
    ).start()

    # Start diagnostics GUI (blocks until window closed)
    general_q.put("SYS: Starting GUI Manager...") # Add queue message
    GuiManager(llm_q, general_q, strategic_q).start()
