"""Every test runs against an empty config dir, never this machine's.

`jiffy.config` reads XDG_CONFIG_HOME once, at import, so this has to happen before
any test module imports jiffy. Without it a box's own `denylist.txt` decides tiers
and a test passes on one machine and fails on the next.
"""
import os
import tempfile

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="jiffy-test-config-")
