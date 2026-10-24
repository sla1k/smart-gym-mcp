"""Tests run against the installed (editable) package and never touch the network or the account.

Shared helpers live in `fakes.py`; `pythonpath = ["tests"]` (pyproject.toml) puts `tests/` on
`sys.path`, so test modules import them as `from fakes import ...`.
"""
