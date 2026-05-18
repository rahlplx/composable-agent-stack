"""Platform adapters for the Composable Agent Stack."""

from orchestrator.adapters.base import PlatformAdapter, AdapterResult
from orchestrator.adapters.agents import AgentSAdapter
from orchestrator.adapters.browser import BrowserUseAdapter
from orchestrator.adapters.openhands import OpenHandsAdapter
from orchestrator.adapters.circuit_breaker import CircuitBreaker, CircuitState, CircuitOpenError

__all__ = [
    "PlatformAdapter", "AdapterResult",
    "AgentSAdapter", "BrowserUseAdapter", "OpenHandsAdapter",
    "CircuitBreaker", "CircuitState", "CircuitOpenError",
]
