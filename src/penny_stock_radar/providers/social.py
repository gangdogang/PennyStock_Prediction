from __future__ import annotations

from pathlib import Path

import pandas as pd


class SocialMentionsCSVProvider:
    def load_mentions(self, path: Path) -> pd.DataFrame:
        frame = pd.read_csv(path, parse_dates=["timestamp"])
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        return frame.sort_values(["symbol", "timestamp"]).reset_index(drop=True)
