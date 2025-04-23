"""
LLM manager module for the Warsim automation framework.
"""
from __future__ import annotations

import logging
import queue
import time
import collections # Add collections for deque
from typing import Dict, List, Optional, Tuple, Union

from google import genai
from google.genai import types

from input_manager import send_number, send_text

# ---------------------------------------------------------------------------
# Public helper tools (exposed to Gemini)
# ---------------------------------------------------------------------------

def send_number_tool(number: int, reasoning: str) -> Dict[str, str]:
    """Choose a numeric menu option (auto‑*Enter*)."""
    return {"status": "success", "action": f"Sent number {number}"}


def send_text_tool(text: str, reasoning: str) -> Dict[str, str]:
    """Send free‑form text (auto‑*Enter*)."""
    truncated = text[:50] + ("…" if len(text) > 50 else "")
    return {"status": "success", "action": f"Sent text: '{truncated}'"}


# Gemini ↔ Python execution bridge ---------------------------------------------------------
_TOOL_EXECUTION_MAP = {
    "send_number_tool": lambda number, reasoning=None: send_number(
        number, append_enter=True
    ),
    "send_text_tool": lambda text, reasoning=None: send_text(
        text, append_enter=True
    ),
}

# OpenAPI‑style declarations ---------------------------------------------------------------
_SEND_NUMBER_DECL = types.FunctionDeclaration(
    name="send_number_tool",
    description="Choose a numeric menu option (auto‑Enter).",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "number": types.Schema(type=types.Type.INTEGER, description="Menu option"),
            "reasoning": types.Schema(type=types.Type.STRING, description="RP justification"),
        },
        required=["number", "reasoning"],
    ),
)

_SEND_TEXT_DECL = types.FunctionDeclaration(
    name="send_text_tool",
    description="Send free‑form text input (auto‑Enter).",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "text": types.Schema(type=types.Type.STRING, description="Input text"),
            "reasoning": types.Schema(type=types.Type.STRING, description="RP justification"),
        },
        required=["text", "reasoning"],
    ),
)

_TOOL_LIST = types.Tool(function_declarations=[_SEND_NUMBER_DECL, _SEND_TEXT_DECL])

# Module constants -------------------------------------------------------------------------
__all__ = ["LLMManager", "send_number_tool", "send_text_tool"]
MAX_HISTORY_TOKENS = 800_000  # 1 M limit minus safety margin
API_CALL_DELAY_S = 4          # ≤ 15 RPM
_LOG = logging.getLogger(__name__)

# System prompt ---------------------------------------------------------------------------
_SYSTEM_PROMPT = (
    "Thou art the newly crowned Sovereign of Aslona. **Speak ONLY in Olde English.** "
    "Use 'thee', 'thou', 'thy', 'hath', 'art', etc. consistently. "
    "Maintain the persona thou adopted at the start of thy reign. "
    "Each turn thou must:\n"
    " 1. Call **exactly one** tool (send_number_tool / send_text_tool).\n"
    " 2. Provide 1‑3 sentences of sovereign justification in the `reasoning` "
    "argument, **using only Olde English phrasing**, remaining in character.\n\n"
    "IMPORTANT GUIDANCE: Avoid customizing reports or viewing reports as they "
    "distract from thy rule. Focus instead on "
    "exploration, diplomacy, and governance."
)

# ---------------------------------------------------------------------------
# LLMManager
# ---------------------------------------------------------------------------

class LLMManager:
    """Ensures Gemini produces *one tool call + short reasoning* each turn."""

    MAX_RETRIES = 3
    REFLECTION_INTERVAL = 20 # Trigger reflection every 20 turns

    def __init__(
        self,
        client: genai.Client,
        model: str,
        llm_q: queue.Queue[Tuple[str, str]] | None = None,
        general_q: queue.Queue[str] | None = None,
    ) -> None:
        """Initialize with client, model, and optional queues."""
        self._client = client
        self._model = model
        self._llm_q = llm_q
        self._general_q = general_q

        self._history: List[types.Content] = []
        self._token_count = 0
        self._turn = 0
        self.last_reasoning: Optional[str] = None
        self.action_taken_this_turn: bool = False # Flag for runner
        self._last_context_buffer: Optional[str] = None # Store context for history
        self.last_reflection_output: Optional[str] = None # Store reflection result
        self._reflection_history: collections.deque[Tuple[str, str, str]] = collections.deque(
            maxlen=self.REFLECTION_INTERVAL
        ) # Stores (state, reasoning, action_str)

    def step(self, context: str) -> None: 
        """Run one game turn: reflect or call Gemini, validate, execute, record."""
        self.action_taken_this_turn = False # Reset flag each step
        self._last_context_buffer = context # Store context for potential history add

        # --- Reflection Logic --- 
        if self._turn > 0 and self._turn % self.REFLECTION_INTERVAL == 0:
            _LOG.info("Turn %d: Performing reflection.", self._turn)
            if self._general_q:
                self._general_q.put(f"SYS: Turn {self._turn} - Performing reflection...")
            self._perform_reflection()
            # Reflection turn replaces action turn, so we return here.
            # The reflection output is stored in self.last_reflection_output
            # and will be used in the *next* step call.
            self._turn += 1 # Increment turn counter even on reflection
            return
        # --- End Reflection Logic ---

        # --- Regular Action Turn --- 
        # Prepare context, potentially adding last reflection output
        current_reflection = self.last_reflection_output
        self.last_reflection_output = None # Clear after use
        self._reset_history(context, reflection=current_reflection)

        cfg = self._base_config()

        for attempt in range(1, self.MAX_RETRIES + 1):
            self._rate_limit(attempt)
            reply = self._call_gemini(cfg, attempt)
            if not reply:
                continue

            func_call = self._extract_func_call(reply, attempt)
            if not func_call:
                continue

            self._process_turn(reply, func_call)
            return

        self._final_failure("Gemini failed to provide a valid tool call.")
        self._turn += 1 # Increment turn even on failure

    # ------------------------ Internal helpers -----------------------------

    def _reset_history(self, user_text: str, reflection: Optional[str] = None) -> None:
        """Reset history, optionally adding reflection output first."""
        self._history = []
        if reflection:
            # Add reflection as a system/assistant message before the main context
            reflection_part = types.Part(text=f"System Note: Thy previous reflection on turns {self._turn - self.REFLECTION_INTERVAL}-{self._turn-1}:\n{reflection}")
            self._history.append(types.Content(role="user", parts=[reflection_part])) # Using 'user' role for simplicity here
            _LOG.debug("Added reflection output to history.")

        # Add the main context
        self._history.append(types.Content(role="user", parts=[types.Part(text=user_text)]))
        self._update_token_count()
        self.last_reasoning = None
        _LOG.debug("Context length %d", len(user_text))

    def _update_token_count(self) -> None:
        try:
            result = self._client.models.count_tokens(model=self._model, contents=self._history)
            self._token_count = result.total_tokens
            if self._token_count > MAX_HISTORY_TOKENS:
                warn = f"⚠️  History size {self._token_count} > {MAX_HISTORY_TOKENS}"
                self._push_llm_line(warn, "warning")
                _LOG.warning(warn)
        except Exception as exc:
            _LOG.error("Token count failed: %s", exc)
            self._token_count = 0

    def _base_config(self) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            tools=[_TOOL_LIST],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode="ANY"
                )
            ),
            system_instruction=_SYSTEM_PROMPT,
        )

    def _rate_limit(self, attempt: int) -> None:
        delay = API_CALL_DELAY_S if attempt == 1 else API_CALL_DELAY_S / 2
        time.sleep(delay)
        if self._general_q:
            self._general_q.put(f"API: Gemini call (attempt {attempt}/{self.MAX_RETRIES})…")

    def _call_gemini(
        self, cfg: types.GenerateContentConfig, attempt: int
    ) -> Optional[types.GenerateContentResponse]:
        try:
            return self._client.models.generate_content(
                model=self._model, contents=self._history, config=cfg
            )
        except Exception as exc:
            _LOG.exception("Gemini API error: %s", exc)
            if attempt == self.MAX_RETRIES:
                self._final_failure(str(exc))
            return None

    def _extract_func_call(
        self, reply: types.GenerateContentResponse, attempt: int
    ) -> Optional[types.PartFunctionCall]:
        parts = reply.candidates[0].content.parts if reply.candidates else []
        func_parts = [p.function_call for p in parts if p.function_call]
        if len(func_parts) == 1:
            return func_parts[0]

        reason = "missing tool" if not func_parts else "multiple tools"
        self._push_llm_line(f"(Attempt {attempt}/{self.MAX_RETRIES} rejected – {reason})", "warning")
        self._add_to_history("model", parts)
        self._add_to_history(
            "user",
            [
                (
                    "System: Attend, Sovereign! Thy response lacketh the required form. "
                    "Thou MUST issue exactly one command (send_number_tool or send_text_tool) "
                    "and provide thy `reasoning`. Pray, attempt this again."
                )
            ],
        )
        return None

    def _process_turn(
        self, reply: types.GenerateContentResponse, func_call: types.PartFunctionCall
    ) -> None:
        parts = reply.candidates[0].content.parts or []
        self._add_to_history("model", parts)

        tool_name = func_call.name
        args: Dict[str, Union[str, int]] = dict(func_call.args)
        reasoning = args.pop("reasoning", "")
        self.last_reasoning = reasoning or "(No reasoning)"

        self._log_reasoning_and_action(tool_name, args, reasoning)
        result = self._execute_tool(tool_name, args)
        self._record_tool_result(tool_name, result)

        # Store turn details for reflection history
        action_details = f"{tool_name}({args})" # Capture action string
        if self._last_context_buffer: # Ensure context buffer is available
            self._reflection_history.append(
                (self._last_context_buffer, self.last_reasoning or "", action_details)
            )
            _LOG.debug("Added turn details to reflection history (size: %d)", len(self._reflection_history))
        else:
            _LOG.warning("Could not add to reflection history: _last_context_buffer was None.")

        self.action_taken_this_turn = True # Mark that an action was processed
        self._turn += 1
        if self._general_q:
            self._general_q.put(f"TURN: {self._turn}")
        _LOG.info("End of turn %d", self._turn)

    def _execute_tool(self, name: str, args: Dict[str, Union[str, int]]) -> Dict[str, str]:
        exec_fn = _TOOL_EXECUTION_MAP.get(name)
        if not exec_fn:
            return {"status": "error", "message": f"Unknown tool '{name}'"}
        try:
            res = exec_fn(**args)
            return res or {"status": "success", "message": "Tool executed"}
        except Exception as exc:
            _LOG.exception("Tool '%s' failed", name)
            if self._general_q:
                self._general_q.put(f"Tool error: {exc}")
            return {"status": "error", "message": str(exc)}

    def _log_reasoning_and_action(self, name: str, args: Dict[str, Union[str, int]], reasoning: str) -> None:
        if reasoning:
            self._push_llm_line(f"LLM Reasoning → {reasoning}", "reasoning")
        self._push_llm_line(f"LLM Action → {name}({args})", "action")

    def _record_tool_result(self, name: str, payload: Dict[str, str]) -> None:
        self._add_to_history(
            "function", [types.Part.from_function_response(name=name, response=payload)]
        )

    def _push_llm_line(self, line: str, msg_type: str = "default") -> None:
        if self._llm_q:
            self._llm_q.put((msg_type, line))

    def _add_to_history(self, role: str, parts: List[types.Part | str]) -> None:
        processed: List[types.Part] = []
        for p in parts:
            if isinstance(p, str):
                processed.append(types.Part(text=p))
            elif isinstance(p, types.Part):
                processed.append(p)
        if processed:
            self._history.append(types.Content(role=role, parts=processed))

    def _final_failure(self, message: str) -> None:
        self._push_llm_line(f"⚠️  {message}", "error")
        if self._general_q:
            self._general_q.put(message)
        _LOG.critical(message)
        self.last_reasoning = "(Failed)"
        # Turn counter incremented in step() for failures

    def _perform_reflection(self) -> None:
        """Calls the LLM to reflect on the past few turns."""
        if not self._reflection_history:
            _LOG.warning("Reflection triggered, but no history available.")
            self.last_reflection_output = "System Note: No history available for reflection."
            return

        # --- Prepare Reflection Prompt ---
        prompt_parts = [
            "System: Thou art the Sovereign of Aslona. It is time to reflect upon thy recent actions. Review the past states of thy realm and thy decisions.",
            "Consider the following sequence of game screens, thy reasoning, and the actions thou took:",
            "--- START REFLECTION HISTORY ---"
        ]
        turn_start_num = self._turn - len(self._reflection_history)
        for i, (state, reasoning, action) in enumerate(self._reflection_history):
            turn_num = turn_start_num + i
            prompt_parts.extend([
                f"**Turn {turn_num}**",
                f"Game Screen:\n```\n{state.strip()}\n```",
                f"Thy Reasoning: {reasoning.strip()}",
                f"Thy Action: `{action}`",
                "---"
            ])
        prompt_parts.extend([
            "--- END REFLECTION HISTORY ---",
            (
                "Now, reflect sovereign. Hast thou made meaningful progress towards thy goals? "
                "Art thou repeating actions without advancing thy position? "
                "If thou find thyself trapped in a cycle, propose a different course of action or a shift in focus for thy next turn. "
                "Speak thy reflection concisely (2-4 sentences)."
            )
        ])
        reflection_prompt = "\n\n".join(prompt_parts)

        # --- Call LLM for Reflection (No Tools) ---
        reflection_config = types.GenerateContentConfig(
            # No tools specified, forcing text-only response
            temperature=0.7
        )
        # Use history mechanism temporarily for the reflection call
        temp_history = [types.Content(role="user", parts=[types.Part(text=reflection_prompt)])]

        _LOG.debug("Calling Gemini for reflection (prompt len: %d)", len(reflection_prompt))
        if self._general_q:
            self._general_q.put("API: Gemini call (Reflection)...")
        time.sleep(API_CALL_DELAY_S) # Rate limit

        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=temp_history,
                config=reflection_config
            )
            if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
                self.last_reflection_output = response.candidates[0].content.parts[0].text.strip()
                _LOG.info("Reflection successful: %s", self.last_reflection_output)
                # Use reasoning style for LLM output queue
                self._push_llm_line(f"LLM Reflection → {self.last_reflection_output}", "reasoning")
                if self._general_q:
                    self._general_q.put(f"SYS: Reflection complete: {self.last_reflection_output}")
            else:
                _LOG.warning("Reflection call failed: No content in response.")
                self.last_reflection_output = "System Note: Reflection generation failed."
                if self._general_q:
                    self._general_q.put("ERR: Reflection generation failed.")
        except Exception as e:
            _LOG.exception("Gemini API error during reflection: %s", e)
            self.last_reflection_output = f"System Note: Reflection failed due to API error: {e}"
            if self._general_q:
                self._general_q.put(f"ERR: Reflection API call failed: {e}")
