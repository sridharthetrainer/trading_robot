from datetime import datetime
from pathlib import Path
class ManualTradeSystem:
    def __init__(self):
        self.trades = {}
        self.db_file = Path("data/manual_trades.json")
    def create_trade(self, **kwargs):
        trade = {**kwargs, "entry_time": datetime.now().isoformat(), "status": "OPEN"}
        self.trades[len(self.trades)+1] = trade
        return len(self.trades)
    def get_all_trades(self):
        return self.trades
manual_trades = ManualTradeSystem()
