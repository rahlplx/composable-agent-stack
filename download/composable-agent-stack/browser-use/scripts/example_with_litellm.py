"""
Browser Use — LiteLLM Integration Example
==========================================
Browser Use connects to LiteLLM via ChatOpenAI with custom base_url.
This makes it work with ANY LLM provider through the unified gateway.

Three methods available (pick one):
  1. ChatOpenAI(base_url=...)     — Simplest, works with any OpenAI-compatible proxy
  2. ChatOpenAILike(base_url=...) — Same as above, alias subclass
  3. ChatLiteLLM(api_base=...)   — Full LiteLLM routing with litellm.acompletion

All three methods route through LiteLLM → your configured provider.
"""

import asyncio
import os
from browser_use import Agent, BrowserConfig
from browser_use.browser.browser import Browser

# ============================================================
# Method 1: ChatOpenAI with base_url (RECOMMENDED — simplest)
# ============================================================
from browser_use.llm.openai.chat import ChatOpenAI

LLM_BASE_URL = os.getenv("OPENAI_BASE_URL", "http://localhost:4000/v1")
LLM_API_KEY = os.getenv("OPENAI_API_KEY", "sk-stack-master-key-change-me")
LLM_MODEL = os.getenv("BROWSER_USE_MODEL", "gpt-4o")

llm = ChatOpenAI(
    model=LLM_MODEL,
    base_url=LLM_BASE_URL,
    api_key=LLM_API_KEY,
)

# ============================================================
# Method 2: ChatLiteLLM (full LiteLLM routing)
# ============================================================
# from browser_use.llm.litellm.chat import ChatLiteLLM
# llm = ChatLiteLLM(
#     model="openai/gpt-4o",              # LiteLLM model routing string
#     api_key="sk-stack-master-key",
#     api_base="http://localhost:4000",    # Note: NO /v1 suffix
# )


# ============================================================
# Example: Web search and summarize
# ============================================================
async def search_and_summarize():
    browser = Browser(config=BrowserConfig(headless=True))
    agent = Agent(
        task="Search for 'best practices for AI agent orchestration 2026' and summarize the top 5 results",
        llm=llm,
        browser=browser,
        max_actions_per_step=5,
    )
    result = await agent.run()
    print(result)
    await browser.close()


# ============================================================
# Example: Form filling
# ============================================================
async def fill_form():
    browser = Browser(config=BrowserConfig(headless=True))
    agent = Agent(
        task="Go to https://httpbin.org/forms/post and fill in the form with: "
             "custname=John Doe, custtel=555-1234, custemail=john@example.com, "
             "size=medium, topping=cheese, topping=mushroom, delivery=13:00, comments=No onions",
        llm=llm,
        browser=browser,
    )
    result = await agent.run()
    print(result)
    await browser.close()


# ============================================================
# Example: Multi-step web workflow
# ============================================================
async def web_workflow():
    browser = Browser(config=BrowserConfig(headless=True))
    agent = Agent(
        task="1. Go to Hacker News (news.ycombinator.com). "
             "2. Find the top 3 stories. "
             "3. For each story, click through and summarize the linked article in 2-3 sentences. "
             "4. Compile all summaries into a single report.",
        llm=llm,
        browser=browser,
        max_actions_per_step=8,
    )
    result = await agent.run()
    print(result)
    await browser.close()


if __name__ == "__main__":
    asyncio.run(search_and_summarize())
