#!/usr/bin/env python3
"""Build cowork/SKILL.md: the Claude Code skill with the engine embedded and a self-unpacking locator."""
import hashlib
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "skills", "colosseum")
OUT = os.path.join(ROOT, "cowork", "SKILL.md")

engine = open(os.path.join(SRC, "colosseum.py"), encoding="utf-8").read()
if "```" in engine:
    raise SystemExit("engine must not contain triple backticks")
sha = hashlib.sha256(engine.encode("utf-8")).hexdigest()
skill = open(os.path.join(SRC, "SKILL.md"), encoding="utf-8").read()

locator = """The engine is embedded at the end of this file (section "Embedded engine", SHA-256 `%s`). Before the first command, make sure it is unpacked:

```bash
E="$HOME/.cache/colosseum/colosseum.py"; SHA=%s
check() { [ -f "$E" ] && [ "$(sha256sum "$E" | cut -d' ' -f1)" = "$SHA" ]; }
if ! check; then
  mkdir -p "$(dirname "$E")"
  for f in $(timeout 60 find / -path /proc -prune -o -name SKILL.md -path "*colosseum*" -print 2>/dev/null | head -20); do
    python3 - "$f" "$E" <<'PY'
import sys
t, f = open(sys.argv[1], encoding="utf-8").read(), "`" * 3
b = "<!-- COLOSSEUM-ENGINE-BEGIN -->\\n" + f + "python\\n"
if b in t:
    s = t.rindex(b) + len(b)
    open(sys.argv[2], "w", encoding="utf-8").write(t[s:t.index(f + "\\n<!-- COLOSSEUM-ENGINE-END -->", s)])
PY
    check && break
  done
fi
check && echo ENGINE_OK || echo ENGINE_MISSING
```

If it prints ENGINE_MISSING, use the Write tool to write the code block under "Embedded engine" to `$HOME/.cache/colosseum/colosseum.py` exactly as it appears, then re-run the check. Never run an engine whose hash does not match.

`COL` below means `python3 "$HOME/.cache/colosseum/colosseum.py"`.""" % (sha, sha)

skill = re.sub(r"<!-- ENGINE-LOCATOR -->.*?<!-- /ENGINE-LOCATOR -->", lambda _m: locator, skill, flags=re.S)
skill += "\n## Embedded engine\n\nDo not edit. Unpacked by the locator above.\n\n<!-- COLOSSEUM-ENGINE-BEGIN -->\n```python\n" + engine + "```\n<!-- COLOSSEUM-ENGINE-END -->\n"
os.makedirs(os.path.dirname(OUT), exist_ok=True)
open(OUT, "w", encoding="utf-8").write(skill)
print("wrote %s (%d bytes, engine sha256 %s)" % (OUT, len(skill), sha))
