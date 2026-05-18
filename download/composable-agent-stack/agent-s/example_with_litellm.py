"""
Agent-S — LiteLLM Integration Example
======================================
Agent-S connects to LiteLLM via OpenAI SDK with custom base_url.
Set engine_type="openai" and point base_url at the LiteLLM proxy.

This makes it work with ANY LLM provider through the unified gateway.
"""

import os
import asyncio

# ============================================================
# Configuration
# ============================================================
LLM_BASE_URL = os.getenv("OPENAI_BASE_URL", "http://localhost:4000/v1")
LLM_API_KEY = os.getenv("OPENAI_API_KEY", "sk-stack-master-key-change-me")
LLM_MODEL = os.getenv("AGENT_S_MODEL", "gpt-4o")


# ============================================================
# Method 1: Programmatic API (recommended)
# ============================================================
async def run_desktop_task():
    """
    Run Agent-S with LiteLLM as the LLM backend.
    Agent-S controls your desktop: mouse, keyboard, screen.
    """
    from gui_agents.s3.core.mllm import LMMAgent

    # Configure the LLM engine to use LiteLLM
    engine_params = {
        "engine_type": "openai",      # Use OpenAI-compatible mode
        "model": LLM_MODEL,           # model_name from LiteLLM config.yaml
        "base_url": LLM_BASE_URL,     # LiteLLM proxy URL
        "api_key": LLM_API_KEY,       # LiteLLM virtual key
    }

    # Create the agent
    agent = LMMAgent(engine_params=engine_params)

    # Execute a desktop task
    result = await agent.execute(
        instruction="Open a web browser and search for 'weather in Dhaka'"
    )
    print(result)


# ============================================================
# Method 2: CLI with custom endpoint
# ============================================================
def run_cli():
    """
    Run Agent-S via CLI with LiteLLM endpoint.
    
    Command:
    python -m gui_agents.s3.cli_app \
        --provider openai \
        --model gpt-4o \
        --model_url http://localhost:4000/v1 \
        --model_api_key sk-stack-master-key-change-me
    """
    import subprocess

    cmd = [
        "python", "-m", "gui_agents.s3.cli_app",
        "--provider", "openai",
        "--model", LLM_MODEL,
        "--model_url", LLM_BASE_URL,
        "--model_api_key", LLM_API_KEY,
    ]
    subprocess.run(cmd)


# ============================================================
# Example: Automated desktop QA
# ============================================================
async def desktop_qa():
    """
    Use Agent-S for automated QA testing of a desktop application.
    The agent will:
    1. Launch the application
    2. Navigate through key features
    3. Take screenshots at each step
    4. Report any UI issues found
    """
    from gui_agents.s3.core.mllm import LMMAgent

    engine_params = {
        "engine_type": "openai",
        "model": LLM_MODEL,
        "base_url": LLM_BASE_URL,
        "api_key": LLM_API_KEY,
    }

    agent = LMMAgent(engine_params=engine_params)

    qa_instruction = """
    Perform a smoke test of the desktop environment:
    1. Open the file manager
    2. Navigate to the Documents folder
    3. Create a new text file called 'test_file.txt'
    4. Open it in a text editor
    5. Type 'Hello, this is a QA test'
    6. Save and close the file
    7. Delete the test file
    8. Close the file manager
    
    Report: SUCCESS if all steps completed, FAIL with details if any step failed.
    """

    result = await agent.execute(instruction=qa_instruction)
    print(result)


if __name__ == "__main__":
    # For CLI usage, uncomment:
    # run_cli()

    # For programmatic usage:
    asyncio.run(run_desktop_task())
