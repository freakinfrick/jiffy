"""The smallest terminal that can answer 'what is still on screen'.

Not an emulator worth the name: it handles the sequences prompt_toolkit actually
emits (CR, LF, CUU/CUD, CUP, ED, EL, SGR) and drops the rest. Enough to count
how many rows of command output a prompt redraw costs the viewer.
"""
import re

CSI = re.compile(r"\x1b\[([0-9;?]*)([A-Za-z])")


class Screen:
    def __init__(self, rows=24, cols=100):
        self.rows, self.cols = rows, cols
        self.buf = [""] * rows
        self.scrollback = []
        self.y = self.x = 0

    def _scroll(self):
        self.scrollback.append(self.buf.pop(0))
        self.buf.append("")
        self.y = self.rows - 1

    def _put(self, ch):
        row = self.buf[self.y].ljust(self.x)
        self.buf[self.y] = row[:self.x] + ch + row[self.x + 1:]
        self.x += 1
        if self.x >= self.cols:
            self.x = 0
            self.y += 1
            if self.y >= self.rows:
                self._scroll()

    def feed(self, text):
        i = 0
        while i < len(text):
            m = CSI.match(text, i)
            if m:
                self._csi(m.group(1), m.group(2))
                i = m.end()
                continue
            ch = text[i]
            i += 1
            if ch == "\r":
                self.x = 0
            elif ch == "\n":
                self.y += 1
                if self.y >= self.rows:
                    self._scroll()
            elif ch == "\x1b":
                i += 1                                  # swallow a two-char escape
            elif ch == "\x08":
                self.x = max(0, self.x - 1)
            elif ch >= " ":
                self._put(ch)

    def _csi(self, args, kind):
        n = int(args) if args.isdigit() else 1
        if kind == "A":
            self.y = max(0, self.y - n)
        elif kind == "B":
            self.y = min(self.rows - 1, self.y + n)
        elif kind == "C":
            self.x = min(self.cols - 1, self.x + n)
        elif kind == "D":
            self.x = max(0, self.x - n)
        elif kind == "G":
            self.x = (int(args) if args.isdigit() else 1) - 1
        elif kind == "H":
            parts = (args or "1;1").split(";")
            self.y = max(0, int(parts[0] or 1) - 1)
            self.x = max(0, int(parts[1] if len(parts) > 1 else 1) - 1)
        elif kind == "J":
            mode = int(args) if args.isdigit() else 0
            if mode == 0:
                self.buf[self.y] = self.buf[self.y][:self.x]
                for r in range(self.y + 1, self.rows):
                    self.buf[r] = ""
            elif mode == 2:
                self.buf = [""] * self.rows
        elif kind == "K":
            mode = int(args) if args.isdigit() else 0
            if mode == 0:
                self.buf[self.y] = self.buf[self.y][:self.x]
            elif mode == 2:
                self.buf[self.y] = ""

    @property
    def visible(self):
        return [r.rstrip() for r in self.buf]

    @property
    def everything(self):
        return [r.rstrip() for r in self.scrollback + self.buf]
