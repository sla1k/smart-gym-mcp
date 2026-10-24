"""Tests run against the installed (editable) package and never touch the network or the account.

Shared helpers live in `fakes.py`; pytest's default rootdir conftest handling puts `tests/` on
`sys.path`, so test modules import them as `from fakes import ...`.
"""
