<p align="center">
  <img src="img/llmplayswarsim.jpg" alt="Project Logo" width="400"/>
</p>

# LLMPlaysWarsim
 LLM Plays Warsim uses Google Gen AI SDK to interact with the game Warsim.

# Quickstart
- Clone project, install requirements. Python 3.9+ Needed, Windows 10+ needed
- Get Gemini API key (free api key available from Google), and either set it in an .env file or system environment variables.
```GOOGLE_API_KEY=KEY_HERE```
- In Warsim, manually disable ASCII in the settings
- Have Warsim already running
- Run the pyw script (```pythonw main.pyw```).

# What this does
- Looks for existing save game (default LLMSave.txt), if it does not exist, quickstarts new game, enables autorecruitment, then passes off to LLM.
- Agent, which automates tasks outside of the LLM, handles things like saving, loading.

Memory system
- Partially implemented, details TBD
