# SPDX-License-Identifier: AGPL-3.0-or-later
"""Offline building blocks for a future Paddle Billing adapter (mock preparation only).

Pure modules: signature verification, typed events/errors, host and key policy and the purchase-state decision
function. Nothing here opens a network connection, reads the database, touches ``app.py`` or grants access.
Verified signatures prove authenticity, not ownership or amount; only an authoritative snapshot plus the
decision rules in ``mapping`` can lead to a state change, and nothing in this package applies one.
"""
