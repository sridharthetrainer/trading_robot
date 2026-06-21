"""
master_contract.py

Lightweight symbol-token resolver for Angel One.

Expected CSV columns may vary. This loader tries to handle common names:
- symbol / tradingsymbol / name
- token / symboltoken
- exch_seg / exchange
"""

from __future__ import annotations

import os
import logging
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


class MasterContract:
    """
    Simple token lookup helper.

    If CSV is missing, methods return None gracefully.
    """

    def __init__(self, csv_file: str = "MasterContract_NFO.csv"):
        self.csv_file = csv_file
        self.df: Optional[pd.DataFrame] = None
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.csv_file):
            logger.warning("Master contract file not found: %s", self.csv_file)
            self.df = None
            return

        try:
            df = pd.read_csv(self.csv_file)
            df.columns = [str(c).strip().lower() for c in df.columns]
            self.df = df
            logger.info("Loaded master contract: %s rows", len(df))
        except Exception as e:
            logger.error("Failed to load master contract %s: %s", self.csv_file, e)
            self.df = None

    def reload(self) -> None:
        self._load()

    def search_scrip(self, symbol: str, exchange: Optional[str] = None) -> Optional[pd.Series]:
        """
        Return matching row for a symbol.
        """
        if self.df is None or self.df.empty:
            return None

        symbol = symbol.strip().upper()

        symbol_cols = [c for c in ["symbol", "tradingsymbol", "name"] if c in self.df.columns]
        exchange_cols = [c for c in ["exch_seg", "exchange"] if c in self.df.columns]

        if not symbol_cols:
            logger.warning("No symbol column found in master contract.")
            return None

        mask = False
        for col in symbol_cols:
            mask = mask | (self.df[col].astype(str).str.upper() == symbol)

        filtered = self.df[mask]

        if exchange and not filtered.empty and exchange_cols:
            ex = exchange.strip().upper()
            ex_mask = False
            for col in exchange_cols:
                ex_mask = ex_mask | (filtered[col].astype(str).str.upper() == ex)
            filtered = filtered[ex_mask]

        if filtered.empty:
            return None

        return filtered.iloc[0]

    def get_token(self, symbol: str, exchange: Optional[str] = None) -> Optional[str]:
        """
        Return token as string if found.
        """
        row = self.search_scrip(symbol, exchange)
        if row is None:
            return None

        for col in ["token", "symboltoken"]:
            if col in row.index and pd.notna(row[col]):
                return str(row[col])

        return None
