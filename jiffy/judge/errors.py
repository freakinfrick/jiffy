"""Typed judge failures. The graph catches these and degrades; nothing here reaches the shell."""


class JudgeError(RuntimeError):
    """Base: any backend failure."""


class JudgeUnavailable(JudgeError):
    """Connection refused, DNS failure, timeout, or retries exhausted. The graph falls through."""


class BudgetExceeded(JudgeError):
    """jiffy's own USD cap (config `usd_cap`) is spent. The graph degrades to a free node."""
