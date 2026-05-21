"""Windows-side RecoveryBoot agent (EFI / NVRAM monitoring; no Recovery Image I/O)."""

from windows_agent.agent import main as agent_main
from windows_agent.windows_state import WindowsState, build_windows_state

__all__ = ["WindowsState", "agent_main", "build_windows_state"]
