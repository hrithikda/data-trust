"""Application services: the only layer the Streamlit UI talks to.

Services return plain dataclasses / dicts (no UI types), hide SQL from the pages and turn
missing prerequisites into :class:`datatrust.errors.DataTrustError` subclasses with an
actionable message.
"""
