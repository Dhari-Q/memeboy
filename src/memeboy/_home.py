import os as _os
MEMEBOY_HOME = _os.environ.get("MEMEBOY_HOME") or _os.path.join(_os.path.expanduser("~"), ".memeboy")
STATE = _os.path.join(MEMEBOY_HOME, "state")
_os.makedirs(STATE, exist_ok=True)
