#!/usr/bin/env python3
"""Refuse paths that a Mac or Windows disk would take for the same one: two files, or two folders on the way to them,
whose names differ only in case. Script modules count without their extension, because an import names them that way:
frontend/src/local/homeVoice.ts and HomeVoice.tsx are both './HomeVoice' there, which broke the Windows build (8 Oct).

    git ls-files | python3 scripts/check-path-case.py
"""
import re
import sys

MODULE = re.compile(r'\.(ts|tsx|js|jsx|mjs|mts)$')


def clashes(paths):
    seen = {}
    for path in paths:
        parts = path.split('/')
        for end in range(1, len(parts) + 1):
            prefix = '/'.join(parts[:end])
            seen.setdefault(prefix.casefold(), set()).add(prefix)
        stem = MODULE.sub('', path)
        if stem != path:
            seen.setdefault(('module', stem.casefold()), set()).add(stem)
    return sorted(sorted(names) for names in seen.values() if len(names) > 1)


if __name__ == '__main__':
    found = clashes(line.rstrip('\n') for line in sys.stdin if line.strip())
    for names in found:
        print('These paths are the same on a Mac or Windows disk: ' + ', '.join(names))
    sys.exit(1 if found else 0)
