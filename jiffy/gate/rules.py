"""Built-in tier rules. Pattern matching on code — nothing here is clever.

A rule is `(rule_id, compiled regex, human reason)`, in that shape, in one of
three ordered tables: `DENY_RULES`, `CONFIRM_RULES`, `NOTE_RULES`. The tables
are plain data: no callbacks, no predicates that could consult the world, no
state. The same line always classifies the same way on any box, offline.

DENY is the floor of the product. Nothing in this module can be turned off by a
flag, a key, a config value or an environment variable; there is no parameter to
any function here that removes a rule from `DENY_RULES`. The user denylist
(`denylist.py`) only ever appends to it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Pattern

from ..types import Tier

__all__ = [
    "Rule", "DENY_RULES", "CONFIRM_RULES", "NOTE_RULES",
    "rules_for", "first_match",
]


@dataclass(frozen=True)
class Rule:
    """One pattern-match rule. Frozen: a loaded rule table is never edited."""

    rule_id: str
    pattern: Pattern[str]
    reason: str

    def search(self, text: str) -> bool:
        return self.pattern.search(text) is not None


def _r(rule_id: str, pattern: str, reason: str) -> Rule:
    return Rule(rule_id, re.compile(pattern, re.IGNORECASE), reason)


# --------------------------------------------------------------------------
# fragments
# --------------------------------------------------------------------------
# `rm` carrying a recursive-ish flag: -r, -R, -rf, -fr, --recursive, --force
# (every one of those flag words contains an "r"). Leading/trailing flags are
# allowed on either side so flag order never matters.
_FLAG = r"(?:\s+-{1,2}[A-Za-z][A-Za-z-]*)"
RM_RECURSIVE = rf"\brm{_FLAG}*\s+-{{1,2}}[A-Za-z-]*[rR][A-Za-z-]*{_FLAG}*"
# end of a command: end of line, or a separator that starts the next one
_END = r"(?:\s|$|[;&|)])"
# the filesystem roots that are never a legitimate recursive-delete target
_SYSTEM_DIRS = (
    r"(?:bin|boot|dev|etc|home|lib|lib32|lib64|media|mnt|opt|proc|root|run|"
    r"sbin|srv|sys|usr|var)"
)
_PIPE_TO_SHELL = r"\|\s*(?:sudo\s+)?(?:/bin/|/usr/bin/)?(?:ba|z|k|da|fi)?sh\b"


# --------------------------------------------------------------------------
# tier 3 — DENY. Never reaches the shell. No override exists.
# --------------------------------------------------------------------------
DENY_RULES: tuple[Rule, ...] = (
    # Interpreter one-liners. A language runtime is a shell with better
    # manners: `python3 -c "shutil.rmtree('/')"` deletes exactly as much as
    # `rm -rf /` does, and none of the rm rules can see it.
    _r("builtin:interpreter-rmtree-root",
       r"\b(?:python[0-9.]*|perl|ruby|node|php)\b[^\n]*"
       r"(?:rmtree|unlink|remove_tree|rimraf|File\.delete)[^\n]*"
       r"[\"']/[\"']",
       "interpreter one-liner deleting the filesystem root"),
    _r("builtin:interpreter-shell-destroy",
       r"\b(?:python[0-9.]*|perl|ruby|node|php)\b[^\n]*"
       r"(?:os\.system|subprocess|popen|exec|`)[^\n]*"
       r"rm\s+-[a-z]*[rR][a-z]*f|\brm\s+-[a-z]*f[a-z]*[rR]\b[^\n]*\s/+\s*$",
       "interpreter one-liner shelling out to a recursive force delete"),
    _r("builtin:rm-rf-root",
       rf"{RM_RECURSIVE}\s+(?:--\s+)?/+\*?{_END}",
       "recursive force delete of the filesystem root"),
    _r("builtin:rm-rf-system-dir",
       rf"{RM_RECURSIVE}\s+(?:--\s+)?/+{_SYSTEM_DIRS}/?\*?{_END}",
       "recursive force delete of a system directory"),
    _r("builtin:rm-rf-home",
       rf"{RM_RECURSIVE}\s+(?:--\s+)?(?:~|\$HOME|\$\{{HOME\}})/?\*?{_END}",
       "recursive force delete of your entire home directory"),
    _r("builtin:rm-rf-wildcard-root",
       rf"{RM_RECURSIVE}\s+(?:--\s+)?/\*",
       "recursive force delete of everything under /"),
    _r("builtin:find-root-delete",
       r"\bfind\s+/(?:\s|\S)*?\s-(?:delete|exec\s+rm)\b",
       "find rooted at / deleting what it walks"),
    _r("builtin:mkfs",
       r"\bmkfs(?:\.[a-z0-9]+)?\b[^;&|]*?/dev/\S+",
       "formatting a block device destroys everything on it"),
    _r("builtin:dd-to-device",
       r"\bdd\b[^;&|]*\bof\s*=\s*/dev/(?:sd|nvme|vd|hd|mmcblk|disk)\S*",
       "dd writing straight onto a disk device"),
    _r("builtin:redirect-to-device",
       r">\s*/dev/(?:sd|nvme|vd|hd|mmcblk|disk)\S*",
       "redirecting output onto a raw disk device"),
    _r("builtin:shred-device",
       r"\bshred\b[^;&|]*\s/dev/\S+",
       "shredding a block device"),
    _r("builtin:wipefs",
       r"\b(?:wipefs|sgdisk\s+(?:-Z|--zap(?:-all)?)|blkdiscard)\b",
       "wiping the partition table or discarding a whole device"),
    _r("builtin:fork-bomb",
       r":\s*\(\s*\)\s*\{\s*:?\s*\|?\s*:?\s*&?\s*\}\s*;?\s*:",
       "fork bomb — it takes the machine down"),
    _r("builtin:fork-bomb-generic",
       r"\b(\w+)\s*\(\s*\)\s*\{[^}]*\1\s*\|\s*\1[^}]*&[^}]*\}",
       "self-recursive function piped into itself — a fork bomb"),
    _r("builtin:curl-pipe-shell",
       rf"\bcurl\b[^;&]*{_PIPE_TO_SHELL}",
       "piping a download straight into a shell runs unread remote code"),
    _r("builtin:wget-pipe-shell",
       rf"\bwget\b[^;&]*{_PIPE_TO_SHELL}",
       "piping a download straight into a shell runs unread remote code"),
    _r("builtin:shell-fetch-substitution",
       r"(?:\b(?:ba|z|k|da|fi)?sh|\bsource|(?:^|[;&|(]\s*)\.)\s+<\(\s*(?:curl|wget)\b"
       r"|\b(?:(?:ba|z|k|da|fi)?sh\s+-c|eval)\s+[\"']?\$\(\s*(?:curl|wget)\b",
       "running a download through a shell (`bash <(curl …)`) runs unread remote code"),
    _r("builtin:fetch-pipe-interpreter",
       r"\b(?:curl|wget)\b[^;&]*\|\s*(?:sudo\s+)?(?:python[0-9.]*|perl|ruby|node)\b",
       "piping a download into an interpreter runs unread remote code"),
    _r("builtin:chmod-777-root",
       rf"\bchmod\b{_FLAG}*\s+(?:-{{1,2}}[A-Za-z-]*[rR][A-Za-z-]*{_FLAG}*\s+)?"
       rf"[0-7]?777\s+/+{_END}",
       "world-writable permissions applied to the whole filesystem"),
    _r("builtin:chmod-recursive-root",
       rf"\bchmod{_FLAG}*\s+-{{1,2}}[A-Za-z-]*[rR][A-Za-z-]*{_FLAG}*\s+\S+\s+/+{_END}",
       "recursive permission change applied to the filesystem root"),
    _r("builtin:chown-recursive-root",
       rf"\bchown{_FLAG}*\s+-{{1,2}}[A-Za-z-]*[rR][A-Za-z-]*{_FLAG}*\s+\S+\s+/+{_END}",
       "recursive ownership change applied to the filesystem root"),
    _r("builtin:kill-everything",
       r"\bkill\b\s+-(?:9|KILL)\s+-1\b",
       "killing every process this user owns"),
    _r("builtin:nvidia-smi-destructive",
       r"\bnvidia-smi\b[^;&|]*\s(?:-r\b|--gpu-reset|-pl\b|--power-limit|"
       r"-e\s|--ecc-config|-pm\s)",
       "nvidia-smi reset / power / ECC change hits every process on the card"),
    _r("builtin:systemctl-stop",
       r"\bsystemctl\b[^;&|]*\s(?:stop|disable|mask|kill)\b",
       "stopping or disabling a systemd unit that other things depend on"),
    _r("builtin:history-wipe",
       r"\bhistory\s+-c\b|\b(?:rm|shred|truncate)\b[^;&|]*\.(?:bash|zsh)_history|"
       r">\s*~?/?\.?(?:bash|zsh)_history",
       "erasing shell history destroys the record of what happened"),
    _r("builtin:git-push-force-default",
       r"\bgit\s+push\b[^;&|]*(?:--force(?!-with-lease)|\s-f\b)"
       r"[^;&|]*\b(?:main|master|trunk)\b",
       "force-pushing a default branch rewrites history for everyone"),
    _r("builtin:git-push-force-default-flagfirst",
       r"\bgit\s+push\b[^;&|]*\b(?:main|master|trunk)\b[^;&|]*"
       r"(?:--force(?!-with-lease)|\s-f\b)",
       "force-pushing a default branch rewrites history for everyone"),
    _r("builtin:crontab-remove",
       r"\bcrontab\s+(?:-u\s+\S+\s+)?-r\b",
       "crontab -r deletes every scheduled job with no confirmation"),
    _r("builtin:mv-root",
       rf"\bmv{_FLAG}*\s+/+\*?\s+\S",
       "moving the filesystem root"),
    _r("builtin:overwrite-etc-passwd",
       r">\s*/etc/(?:passwd|shadow|sudoers|fstab)\b",
       "overwriting a file the system needs to boot or log you in"),
)


# --------------------------------------------------------------------------
# tier 2 — CONFIRM. Red banner naming the reason; a second ENTER runs it.
# --------------------------------------------------------------------------
CONFIRM_RULES: tuple[Rule, ...] = (
    # Any interpreter -c/-e one-liner that touches the filesystem destructively.
    # Below DENY because the target is usually scoped, but never silent.
    _r("builtin:interpreter-destructive",
       r"\b(?:python[0-9.]*|perl|ruby|node|php)\b\s+-(?:c|e)\b[^\n]*"
       r"(?:rmtree|shutil\.|os\.remove|os\.unlink|os\.rmdir|unlink\(|"
       r"remove_tree|rimraf|truncate|chmod|chown)",
       "interpreter one-liner performing a destructive filesystem operation"),
    _r("builtin:rm-recursive",
       rf"{RM_RECURSIVE}\s+\S",
       "recursive delete"),
    _r("builtin:sudo",
       r"(?:^|[;&|]\s*)sudo\b|\bsudo\s+-\S",
       "runs as root"),
    _r("builtin:doas",
       r"(?:^|[;&|]\s*)(?:doas|pkexec)\b",
       "runs as root"),
    _r("builtin:package-install",
       r"\b(?:apt|apt-get|aptitude|dpkg|yum|dnf|zypper|pacman|snap|flatpak|brew|"
       r"pip|pip3|pipx|npm|pnpm|yarn|cargo|gem|go)\b[^;&|]*\b"
       r"(?:install|uninstall|remove|purge|erase|add|-S|-R|-U)\b",
       "installs or removes packages"),
    _r("builtin:kill",
       r"\b(?:kill|pkill|killall|skill)\b\s+\S",
       "kills running processes"),
    _r("builtin:chmod-chown",
       r"\b(?:chmod|chown|chgrp|setfacl)\b\s+\S",
       "changes permissions or ownership"),
    _r("builtin:write-outside-cwd",
       r"(?:^|\s)>>?\s*(?:/|~/|\$HOME)",
       "writes to a path outside the working directory"),
    _r("builtin:copy-outside-cwd",
       r"\b(?:cp|mv|rsync|tee|ln|install)\b[^;&|]*\s(?:/|~/)[^\s;&|]*\s*(?:$|[;&|])",
       "writes to a path outside the working directory"),
    _r("builtin:mkdir-outside-cwd",
       r"\b(?:mkdir|touch|truncate|dd)\b[^;&|]*\s(?:/|~/)[^\s;&|]*",
       "creates or truncates a path outside the working directory"),
    _r("builtin:network-upload",
       r"\bcurl\b[^;&|]*\s(?:-T\b|--upload-file|-F\b|--form|-d\b|--data\b|"
       r"--data-binary|-X\s*(?:POST|PUT|PATCH|DELETE))",
       "uploads data to the network"),
    _r("builtin:scp-upload",
       r"\b(?:scp|sftp|rsync)\b[^;&|]*\s\S+\s+\S+:",
       "copies files to a remote host"),
    _r("builtin:cloud-upload",
       r"\b(?:aws\s+s3|gsutil|rclone|az\s+storage)\b[^;&|]*\b(?:cp|sync|mv|put)\b",
       "uploads to cloud storage"),
    _r("builtin:git-push",
       r"\bgit\s+push\b",
       "publishes commits to a remote"),
    _r("builtin:git-destructive",
       r"\bgit\s+(?:reset\s+--hard|clean\s+-\S*[fd]|checkout\s+--\s|"
       r"branch\s+-D|rebase\b|filter-branch|stash\s+drop|stash\s+clear)",
       "discards or rewrites local git state"),
    _r("builtin:mount",
       r"\b(?:mount|umount|swapoff|swapon)\b\s+\S",
       "changes what is mounted"),
    _r("builtin:firewall",
       r"\b(?:iptables|nft|ufw|firewall-cmd)\b\s+\S",
       "changes firewall rules"),
    _r("builtin:service-start",
       r"\b(?:systemctl|service)\b[^;&|]*\s(?:start|restart|reload|enable)\b",
       "changes a system service"),
    _r("builtin:docker-destructive",
       r"\bdocker\b[^;&|]*\b(?:rm|rmi|prune|kill|stop|down)\b",
       "removes or stops containers"),
    _r("builtin:user-admin",
       r"\b(?:useradd|userdel|usermod|groupadd|groupdel|passwd|visudo)\b",
       "changes accounts or credentials"),
    _r("builtin:crontab-edit",
       r"\bcrontab\b\s+(?!-l\b)\S",
       "changes scheduled jobs"),
    _r("builtin:dd",
       r"\bdd\b[^;&|]*\bof\s*=",
       "dd overwrites its destination in place"),
    _r("builtin:vpn",
       r"\b(?:openvpn|wg-quick|tailscale\s+up)\b",
       "brings up a VPN — a system-wide tunnel can cut this session off"),
)


# --------------------------------------------------------------------------
# tier 1 — NOTE. Yellow tag; runs normally.
# --------------------------------------------------------------------------
NOTE_RULES: tuple[Rule, ...] = (
    _r("builtin:pipe-to-interpreter",
       rf"{_PIPE_TO_SHELL}|\|\s*(?:python[0-9.]*|perl|ruby|node|awk)\b",
       "pipes into a shell interpreter"),
    _r("builtin:eval",
       r"\b(?:eval|source|\.)\s+\S|\$\(|`",
       "evaluates constructed text as code"),
    _r("builtin:delete",
       r"\b(?:rm|unlink|shred|trash)\b\s+\S",
       "deletes files"),
    _r("builtin:in-place-edit",
       r"\bsed\b[^;&|]*\s-\S*i\S*(?:\s|$)|\bperl\b[^;&|]*\s-\S*i",
       "edits files in place"),
    _r("builtin:modifies",
       r"\b(?:mv|cp|touch|mkdir|rmdir|ln|tee|truncate|rename|patch|chmod)\b\s+\S",
       "modifies the filesystem"),
    _r("builtin:redirect",
       r"(?:^|\s)>>?\s*\S",
       "writes to a file"),
    _r("builtin:vcs-write",
       r"\bgit\s+(?:commit|add|merge|apply|am|cherry-pick|tag|checkout|switch|"
       r"restore|pull|fetch|clone|init)\b",
       "changes the repository state"),
    _r("builtin:download",
       r"\b(?:curl|wget|aria2c|yt-dlp|youtube-dl)\b\s+\S",
       "downloads from the network"),
    _r("builtin:long-running",
       # command position only: `man tar` reads about tar, it does not run it.
       r"(?:^|[;&|]\s*|\bsudo\s+|\btime\s+)(?:make|cmake|ninja|cargo\s+build|"
       r"docker\s+build|dd|rsync|tar|7z|zip|unzip|gzip|bzip2|xz|ffmpeg|sleep|"
       r"pytest|npm\s+run)\b",
       "may run for a long time"),
    _r("builtin:whole-fs-walk",
       r"\b(?:find|grep|rg|du|locate)\b[^;&|]*\s/(?:\s|$)|\bupdatedb\b",
       "walks the whole filesystem — slow"),
    _r("builtin:background",
       r"\bnohup\b|&\s*$|\bdisown\b|\bsetsid\b",
       "runs in the background"),
    _r("builtin:writes-config",
       r"\b(?:npm|pip|pip3|git)\s+config\b|\bupdate-alternatives\b",
       "changes tool configuration"),
)


_TABLES: dict[Tier, tuple[Rule, ...]] = {
    Tier.DENY: DENY_RULES,
    Tier.CONFIRM: CONFIRM_RULES,
    Tier.NOTE: NOTE_RULES,
}


def rules_for(tier: Tier) -> tuple[Rule, ...]:
    """The built-in table for one tier. Returns the frozen tuple, never a copy
    anything could mutate into a smaller set."""
    return _TABLES.get(Tier(tier), ())


def first_match(texts, rules) -> Rule | None:
    """First rule in `rules` matching any of `texts`. Order is the table's order."""
    for rule in rules:
        for text in texts:
            if rule.search(text):
                return rule
    return None
