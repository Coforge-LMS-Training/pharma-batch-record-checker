"""Synthetic data generator for the Batch Record Review Checker (owner: Sarthak).

Builds the approved masters, clean executed batches, planted defects and the
matching expected_findings.csv, all from a seed. It uses the shared CSV
contract (`pharma_batch_record_checker.schema` / `.csv_io`) and never imports
the app's parser, rules engine or store.

This file deliberately imports nothing, so importing a generator module loads
only what that module needs.
"""
