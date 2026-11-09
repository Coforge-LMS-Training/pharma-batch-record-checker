"""Postgres store for masters, batches, review runs, notes, and the hash chain.

Reads and the auditor CSV are selects. A review problem is stored without a
hash event. The store does not import the generator, and it does not apply
review rules.
"""
