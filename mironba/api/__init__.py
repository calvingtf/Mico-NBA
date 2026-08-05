"""Presentation layer. Reads committed artifacts; computes nothing.

The import fence (tests/test_ui.py) keeps mironba.sim, mironba.models and
mironba.eval out of this package entirely - a UI that can reach the
simulation can quietly become a second results pipeline.
"""
