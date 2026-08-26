"""Materialization helpers for graph-native durable dataflow."""

from ads.dataflow.tables import load_table_asset, persist_table_asset

__all__ = ["load_table_asset", "persist_table_asset"]
