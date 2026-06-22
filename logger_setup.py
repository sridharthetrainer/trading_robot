"""logger_setup.py — compatibility stub (standard logging used instead)"""
import logging

def setup_logger(name: str = __name__, level: int = logging.INFO) -> logging.Logger:
    return logging.getLogger(name)
