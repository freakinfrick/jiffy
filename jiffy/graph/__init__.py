"""The one pipeline: tournament -> pick -> invoke -> critic -> compose -> fill -> gate.

    from jiffy.graph import answer
    a = answer("find big log files")
    print(a.assembled.comment_line)
    print(a.trace.render())                   # jiffy --explain
"""
from .api import answer, build, corpus, reset_cache
from .assemble import assemble, placeholders
from .pipeline import Pipeline, Run, STAGES

__all__ = ["answer", "build", "corpus", "reset_cache", "Pipeline", "Run", "STAGES",
           "assemble", "placeholders"]
