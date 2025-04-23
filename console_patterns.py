import re

MAIN_MENU_RE     = re.compile(r"Welcome to Warsim.*?1\) Start a New Game", re.S)
LOAD_MENU_RE     = re.compile(r"Savegames.*enter the name of the save file", re.I | re.S)
PRESS_ANY_KEY_RE = re.compile(r"Press any key to continue", re.I)
KINGDOM_MENU_RE  = re.compile(r"KINGDOM MENU", re.I)
# Pattern to detect the screen shown when auto-recruit is OFF
AUTORECRUIT_SETUP_PROMPT_RE = re.compile(r"automate the automation for me!", re.I | re.S)
# Pattern to detect the screen shown when auto-recruit is ON
AUTORECRUIT_ALREADY_ON_RE = re.compile(r"already recruiting automatically", re.I | re.S)
# Pattern to detect the start of an arena fight (e.g., "  Knight vs. Bandit")
ARENA_FIGHT_START_RE = re.compile(r"^\s+\S+\s+vs\.\s+\S+", re.I)