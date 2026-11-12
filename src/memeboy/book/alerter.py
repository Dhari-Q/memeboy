#!/usr/bin/env python
"""Quant machine: alerter. If the brain left state/notify.txt, print its
content (delivered to origin) and clear it. Silent otherwise."""
import os
# MEMEBOY_HOME: where state/*.json lives (default ~/.memeboy).
MEMEBOY_HOME = os.environ.get("MEMEBOY_HOME") or os.path.join(os.path.expanduser("~"), ".memeboy")
REPO = MEMEBOY_HOME
STATE = os.environ.get("QM_STATE") or os.path.join(REPO, "state")
os.makedirs(STATE, exist_ok=True)


NOTIFY = r"C:\Users\dhari\projects\crypto-research\state\notify.txt"


def main():
    if not os.path.exists(NOTIFY):
        return
    with open(NOTIFY, "r", encoding="utf-8") as fh:
        text = fh.read().strip()
    if text:
        print(text)
        os.remove(NOTIFY)


if __name__ == "__main__":
    main()
