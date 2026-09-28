# core/logging.py
"""Shared logging utilities for the CLI application."""

from rich.console import Console

console = Console()

# Verbose mode flag (set via CLI)
_verbose = False

# Brave mode flag (set via CLI)
_brave = False


def set_verbose(enabled: bool):
    """Set verbose mode for debug output."""
    global _verbose
    _verbose = enabled


def is_verbose() -> bool:
    """Check if verbose mode is enabled."""
    return _verbose


def set_brave(enabled: bool):
    """Set brave mode for ASP reasoning."""
    global _brave
    _brave = enabled


def is_brave() -> bool:
    """Check if brave mode is enabled."""
    return _brave


def debug_print(text: str):
    """Print debug output in dim style (only when verbose mode is enabled)."""
    if _verbose:
        console.print(f"[dim]{text}[/dim]")


def ai_print(text: str):
    """Print AI responses in green."""
    console.print(text, style="green")
