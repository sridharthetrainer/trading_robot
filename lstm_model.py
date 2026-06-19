"""
lstm_model.py

LSTM sequence model for price pattern recognition.

Why LSTM over XGBoost for this task:
    XGBoost learns from a single row of features per trade.
    LSTM learns from sequences of 30 price bars — it can detect
    patterns like bull flags, wedges, and head-and-shoulders that
    are invisible to tabular models.

Architecture:
    Input:  30-bar sequence of (Close, Volume, RSI, ATR, ADX, MACD_hist)
            → shape (batch, 30, 6)
    LSTM:   2 layers, 64 hidden units
    Dense:  64 → 32 → 1 (sigmoid)
    Output: probability that the next 12 bars will move > 1.5%

Training:
    - Nightly on last 120 days of 5-minute data for each symbol
    - Binary labels: 1 if max(future 12 bars) > entry + 1.5%
    - Class-weighted loss to handle imbalanced labels
    - Early stopping on validation loss

Usage:
    from lstm_model import LSTMPredictor
    model = LSTMPredictor()
    model.train(df_5min, symbol="NIFTY")
    prob = model.predict(df_5min.tail(30))  # → float 0.0-1.0
    # prob > 0.60 = strong signal, incorporate into final score
"""
from __future__ import annotations


# Auto-fix: get DataFetcher with Angel singleton
def _get_angel_data_fetcher():
    try:
        from angel import AngelOne
        import os as _os_adf
        _ang = AngelOne(api_key=_os_adf.getenv("API_KEY",""),
            client_id=_os_adf.getenv("CLIENT_ID",""),
            password=_os_adf.getenv("PASSWORD",""),
            totp_secret=_os_adf.getenv("TOTP_SECRET",""))
    except Exception: _ang = None
    from data_fetcher import DataFetcher
    return DataFetcher(angel=_ang, paper_trade=False)


import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────
SEQ_LEN         = 30       # input sequence length (30 × 5min = 2.5 hours)
N_FEATURES      = 6        # Close, Volume, RSI, ATR, ADX, MACD_hist
HIDDEN_SIZE     = 64
NUM_LAYERS      = 2
DROPOUT         = 0.2
EPOCHS          = 50
BATCH_SIZE      = 64
LR              = 0.001
EARLY_STOP_PAT  = 8        # stop if val_loss doesn't improve for 8 epochs
TARGET_MOVE_PCT = 0.015    # 1.5% move in 12 bars = positive label
LABEL_HORIZON   = 12       # bars to look ahead
MIN_SAMPLES     = 500      # minimum training samples required
MODEL_DIR       = "models"


def _check_torch() -> bool:
    try:
        import torch  # noqa
        return True
    except ImportError:
        return False


# ── Feature engineering ───────────────────────────────────────────────────────

def build_features(df: pd.DataFrame) -> Optional[np.ndarray]:
    """
    Build the 6-feature matrix from an OHLCV DataFrame.
    Returns shape (n_bars, 6) or None if insufficient data.
    """
    try:
        from indicators import (
            calculate_rsi, calculate_atr, calculate_adx, calculate_macd
        )

        if df is None or len(df) < SEQ_LEN + LABEL_HORIZON + 20:
            return None

        close = pd.to_numeric(df["Close"] if "Close" in df.columns else df["close"],
                              errors="coerce")
        volume = pd.to_numeric(df.get("Volume", df.get("volume", pd.Series(1, index=df.index))),
                               errors="coerce").fillna(1)

        rsi   = calculate_rsi(df, 14).fillna(50)
        atr   = calculate_atr(df, 14).fillna(close * 0.005)
        adx   = calculate_adx(df, 14).fillna(20)
        _, _, hist = calculate_macd(df)
        hist  = hist.fillna(0)

        # Normalise features to [0, 1] range
        close_norm  = close.pct_change().fillna(0).clip(-0.05, 0.05) / 0.10 + 0.5
        vol_norm    = (volume / volume.rolling(20).mean().replace(0, 1)).clip(0, 5) / 5
        rsi_norm    = rsi / 100
        atr_norm    = (atr / close.replace(0, 1)).clip(0, 0.03) / 0.03
        adx_norm    = adx / 100
        hist_norm   = (hist / close.replace(0, 1) * 100).clip(-1, 1) / 2 + 0.5

        mat = np.column_stack([
            close_norm.values,
            vol_norm.values,
            rsi_norm.values,
            atr_norm.values,
            adx_norm.values,
            hist_norm.values,
        ])
        return mat.astype(np.float32)

    except Exception as exc:
        logger.debug("build_features error: %s", exc)
        return None


def build_sequences(
    features: np.ndarray,
    df:       pd.DataFrame,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Build (X, y) training pairs from the feature matrix.
    X shape: (n, SEQ_LEN, N_FEATURES)
    y shape: (n,)  — binary label
    """
    close_col = "Close" if "Close" in df.columns else "close"
    close     = pd.to_numeric(df[close_col], errors="coerce").values

    X_list, y_list = [], []
    n = len(features)

    for i in range(SEQ_LEN, n - LABEL_HORIZON):
        seq      = features[i - SEQ_LEN : i]
        entry    = close[i]
        future   = close[i : i + LABEL_HORIZON]

        if entry <= 0 or np.any(np.isnan(seq)):
            continue

        # Label: 1 if price moves up > TARGET_MOVE_PCT in any future bar
        max_up = (np.nanmax(future) - entry) / entry
        label  = 1 if max_up >= TARGET_MOVE_PCT else 0

        X_list.append(seq)
        y_list.append(label)

    if not X_list:
        return np.array([]), np.array([])

    return np.array(X_list, dtype=np.float32), np.array(y_list, dtype=np.float32)


# ── PyTorch model ─────────────────────────────────────────────────────────────

def _build_model():
    """Build and return the PyTorch LSTM model."""
    import torch
    import torch.nn as nn

    class LSTMNet(nn.Module):
        def __init__(self):
            super().__init__()
            self.lstm = nn.LSTM(
                input_size   = N_FEATURES,
                hidden_size  = HIDDEN_SIZE,
                num_layers   = NUM_LAYERS,
                batch_first  = True,
                dropout      = DROPOUT if NUM_LAYERS > 1 else 0,
            )
            self.head = nn.Sequential(
                nn.Linear(HIDDEN_SIZE, 32),
                nn.ReLU(),
                nn.Dropout(DROPOUT),
                nn.Linear(32, 1),
                nn.Sigmoid(),
            )

        def forward(self, x):
            out, _ = self.lstm(x)
            return self.head(out[:, -1, :]).squeeze(1)

    return LSTMNet()


# ── Training ──────────────────────────────────────────────────────────────────

def train_lstm(
    df:     pd.DataFrame,
    symbol: str = "NIFTY",
) -> Optional[Dict[str, Any]]:
    """
    Train the LSTM model on historical 5-minute data.

    Returns a result dict:
    {
        "trained": bool,
        "val_accuracy": float,
        "val_auc": float,
        "epochs": int,
        "model_path": str,
    }
    """
    if not _check_torch():
        logger.warning("PyTorch not installed — LSTM model unavailable. "
                       "Install with: pip install torch")
        return {"trained": False, "reason": "torch_not_installed"}

    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset

    features = build_features(df)
    if features is None:
        return {"trained": False, "reason": "insufficient_features"}

    X, y = build_sequences(features, df)
    if len(X) < MIN_SAMPLES:
        return {"trained": False,
                "reason": f"only {len(X)} samples, need {MIN_SAMPLES}"}

    # Train/validation split (80/20, no shuffle to preserve time order)
    split = int(len(X) * 0.80)
    X_tr, X_val = X[:split], X[split:]
    y_tr, y_val = y[:split], y[split:]

    X_tr_t  = torch.tensor(X_tr)
    y_tr_t  = torch.tensor(y_tr)
    X_val_t = torch.tensor(X_val)
    y_val_t = torch.tensor(y_val)

    # Class weights to handle imbalance
    pos_rate = y_tr.mean()
    pos_wt   = (1 - pos_rate) / max(pos_rate, 1e-6)
    weights  = torch.where(y_tr_t == 1, torch.tensor(pos_wt), torch.tensor(1.0))

    dataset    = TensorDataset(X_tr_t, y_tr_t, weights)
    loader     = DataLoader(dataset, batch_size=BATCH_SIZE, shuffle=True)

    model    = _build_model()
    opt      = torch.optim.Adam(model.parameters(), lr=LR)
    criterion= nn.BCELoss(reduction="none")

    best_val_loss = float("inf")
    best_state    = None
    patience      = 0

    for epoch in range(1, EPOCHS + 1):
        model.train()
        for xb, yb, wb in loader:
            pred = model(xb)
            loss = (criterion(pred, yb) * wb).mean()
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()

        # Validation
        model.eval()
        with torch.no_grad():
            val_pred = model(X_val_t)
            val_loss = criterion(val_pred, y_val_t).mean().item()
            val_acc  = ((val_pred > 0.5).float() == y_val_t).float().mean().item()

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_state    = {k: v.clone() for k, v in model.state_dict().items()}
            patience      = 0
        else:
            patience += 1
            if patience >= EARLY_STOP_PAT:
                logger.debug("LSTM early stop at epoch %d", epoch)
                break

    if best_state:
        model.load_state_dict(best_state)

    # Save model
    Path(MODEL_DIR).mkdir(exist_ok=True)
    model_path = os.path.join(MODEL_DIR, f"lstm_{symbol.lower()}.pt")
    torch.save({"state_dict": model.state_dict(),
                "val_accuracy": val_acc,
                "seq_len": SEQ_LEN, "n_features": N_FEATURES}, model_path)

    logger.info("LSTM trained | symbol=%s val_acc=%.3f model=%s",
                symbol, val_acc, model_path)
    return {
        "trained":      True,
        "val_accuracy": round(float(val_acc), 4),
        "model_path":   model_path,
        "epochs_run":   epoch,
    }


# ── Inference ─────────────────────────────────────────────────────────────────

class LSTMPredictor:
    """
    Wraps the trained LSTM model for real-time inference.

    Usage:
        predictor = LSTMPredictor()
        predictor.load("NIFTY")
        prob = predictor.predict(df_last_30_bars)
        # Incorporate into signal score: if prob > 0.60, boost by +0.5
    """

    def __init__(self) -> None:
        self._models: Dict[str, Any] = {}
        self._available = _check_torch()

    def load(self, symbol: str) -> bool:
        """Load saved LSTM model for a symbol. Returns True if successful."""
        if not self._available:
            return False
        import torch
        path = os.path.join(MODEL_DIR, f"lstm_{symbol.lower()}.pt")
        if not os.path.exists(path):
            return False
        try:
            checkpoint = torch.load(path, map_location="cpu")
            model = _build_model()
            model.load_state_dict(checkpoint["state_dict"])
            model.eval()
            self._models[symbol] = model
            logger.debug("LSTM loaded for %s", symbol)
            return True
        except Exception as exc:
            logger.warning("LSTM load failed for %s: %s", symbol, exc)
            return False

    def predict(
        self,
        df:     pd.DataFrame,
        symbol: str = "NIFTY",
    ) -> float:
        """
        Predict probability of a significant upward move.
        Returns 0.5 (neutral) if model not loaded or insufficient data.
        """
        if not self._available:
            return 0.5
        if symbol not in self._models:
            if not self.load(symbol):
                return 0.5

        import torch
        try:
            features = build_features(df)
            if features is None or len(features) < SEQ_LEN:
                return 0.5

            seq   = features[-SEQ_LEN:]
            x     = torch.tensor(seq).unsqueeze(0)  # (1, 30, 6)
            model = self._models[symbol]
            with torch.no_grad():
                prob = float(model(x).item())
            return round(prob, 4)
        except Exception as exc:
            logger.debug("LSTM predict error for %s: %s", symbol, exc)
            return 0.5

    def get_signal_boost(self, symbol: str, df: pd.DataFrame) -> float:
        """
        Returns a score boost for use in signal_engine.
        Returns 0.0 if model not available.
        Mapping:
            prob > 0.70 → +1.0 boost
            prob > 0.60 → +0.5 boost
            prob < 0.40 → -0.5 penalty
            prob < 0.30 → -1.0 penalty
        """
        prob = self.predict(df, symbol)
        if prob > 0.70:  return  1.0
        if prob > 0.60:  return  0.5
        if prob < 0.30:  return -1.0
        if prob < 0.40:  return -0.5
        return 0.0

    def status(self) -> Dict[str, Any]:
        return {
            "torch_available": self._available,
            "loaded_symbols":  list(self._models.keys()),
            "model_dir":       MODEL_DIR,
        }


# ── Module-level singleton ────────────────────────────────────────────────────
_predictor: Optional[LSTMPredictor] = None


def get_lstm_predictor() -> LSTMPredictor:
    global _predictor
    if _predictor is None:
        _predictor = LSTMPredictor()
    return _predictor


# ── CLI: train all symbols ────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)s | %(message)s")

    parser = argparse.ArgumentParser(description="Train LSTM model")
    parser.add_argument("--symbol", default="NIFTY")
    parser.add_argument("--days",   type=int, default=120)
    args = parser.parse_args()

    print(f"Training LSTM for {args.symbol} on {args.days} days of data...")

    try:
        from data_fetcher import DataFetcher
        df = _get_angel_data_fetcher().get_market_data(args.symbol, interval="5m", days=args.days)
    except Exception:
        import yf_compat as yf  # yfinance replaced: Yahoo API broken
        sym = "^NSEI" if args.symbol == "NIFTY" else f"{args.symbol}.NS"
        df  = yf.download(sym, period=f"{args.days}d", interval="5m",
                          progress=False, auto_adjust=True)

    if df is None or len(df) < 500:
        print("Not enough data — need at least 120 days of 5-minute bars")
    else:
        result = train_lstm(df, symbol=args.symbol)
        if result["trained"]:
            print(f"✅ Model trained | val_accuracy={result['val_accuracy']:.1%} "
                  f"| saved to {result['model_path']}")
        else:
            print(f"❌ Training failed: {result.get('reason')}")
