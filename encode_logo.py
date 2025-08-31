#!/usr/bin/env python3
"""Manually embed logo.png into SVG files as base64 data URI.
Usage:
  python encode_logo.py                 # updates default SVG files
  python encode_logo.py dark.svg light.svg

On success prints the data URI length. Idempotent.
"""
from __future__ import annotations
import sys, base64, re, pathlib

LOGO = pathlib.Path('logo.png')
DEFAULT_SVGS = ['dark.svg','light.svg']

# Precompiled patterns
LOGO_PATTERN = re.compile(r'href=("|\')logo\.png\1')
DATA_URI_PATTERN = re.compile(r'href=("|\')data:image/png;base64,[A-Za-z0-9+/=]+\1')

def main():
    if not LOGO.exists():
        print('logo.png not found')
        return 1
    b64 = base64.b64encode(LOGO.read_bytes()).decode('ascii')
    data_uri = f'data:image/png;base64,{b64}'
    svgs = sys.argv[1:] or DEFAULT_SVGS
    replaced_any = False
    for svg in svgs:
        p = pathlib.Path(svg)
        if not p.exists():
            print(f'skip missing {svg}')
            continue
        txt = p.read_text(encoding='utf-8')
        new_txt, n = LOGO_PATTERN.subn(f'href="{data_uri}"', txt)
        if n == 0:
            new_txt, n = DATA_URI_PATTERN.subn(f'href="{data_uri}"', txt)
        if n:
            p.write_text(new_txt, encoding='utf-8')
            replaced_any = True
            print(f'updated {svg} (embedded logo)')
        else:
            print(f'no change {svg}')
    print(f'data URI length: {len(data_uri)} chars')
    return 0 if replaced_any else 0

if __name__ == '__main__':
    raise SystemExit(main())
