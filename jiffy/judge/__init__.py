"""Judge backends behind one adapter interface.

Importing this package registers: jev, stub.
"""
from . import backends as _backends  # noqa: F401  (registers adapters)
from .base import JudgeAdapter, available, get, register
from .errors import BudgetExceeded, JudgeError, JudgeUnavailable

__all__ = ["JudgeAdapter", "get", "register", "available",
           "JudgeError", "JudgeUnavailable", "BudgetExceeded"]
