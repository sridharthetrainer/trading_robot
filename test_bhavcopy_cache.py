import io
import zipfile
from datetime import date

import bhavcopy_cache as bc


class _FakeResponse:
    def __init__(self, status_code, content):
        self.status_code = status_code
        self.content = content


def _zip_csv(csv_text: str, inner_name: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(inner_name, csv_text)
    return buf.getvalue()


def _padded(csv_text: str, min_bytes: int = 1500) -> str:
    """download_bhavcopy requires len(content) > 1000 to reject error/empty
    pages; pad synthetic fixtures with harmless extra rows past that floor."""
    lines = csv_text.splitlines()
    header, rows = lines[0], lines[1:]
    i = 0
    while len(("\n".join([header] + rows)) + "\n") < min_bytes:
        rows.append(rows[0].replace(rows[0].split(",")[0], f"FILLER{i}"))
        i += 1
    return "\n".join([header] + rows) + "\n"


def test_download_bhavcopy_parses_plain_csv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    csv_text = _padded(
        "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,TOTTRDQTY\n"
        "RELIANCE,EQ,100,105,99,102,1000\n"
    )

    class _FakeSession:
        def __init__(self):
            self.headers = {}
        def update(self, *a, **kw):
            pass
        def get(self, url, timeout=15):
            if url == bc._BHV_URL.format(date(2026, 1, 15).strftime("%d%m%Y")):
                return _FakeResponse(200, csv_text.encode())
            return _FakeResponse(404, b"")

    fake = _FakeSession()
    monkeypatch.setattr(bc.requests if hasattr(bc, "requests") else bc, "Session", lambda: fake, raising=False)
    import requests
    monkeypatch.setattr(requests, "Session", lambda: fake)

    n = bc.download_bhavcopy(date(2026, 1, 15))
    assert n >= 1
    df = bc.get_ohlcv("RELIANCE", days=3650)
    assert df is not None and len(df) == 1
    assert df.iloc[0]["close"] == 102


def test_download_bhavcopy_parses_classic_zip_format(tmp_path, monkeypatch):
    """2026-07-29: a 10-year backfill needs the pre-2024 classic per-day ZIP
    archive format too, since NSE's flat CSV format only covers recent years."""
    monkeypatch.chdir(tmp_path)
    csv_text = _padded(
        "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,"
        "TIMESTAMP,TOTALTRADES,ISIN\n"
        "TCS,EQ,2000,2050,1990,2010,2010,2005,500,1000000,15-JAN-2018,200,INE467B01029\n"
    )
    zip_bytes = _zip_csv(csv_text, "cm15JAN2018bhav.csv")
    # zip compression could shrink repetitive padding below the 1000-byte
    # floor download_bhavcopy checks on raw response bytes -- store uncompressed.
    if len(zip_bytes) <= 1000:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr("cm15JAN2018bhav.csv", csv_text)
        zip_bytes = buf.getvalue()

    class _FakeSession:
        def __init__(self):
            self.headers = {}
        def update(self, *a, **kw):
            pass
        def get(self, url, timeout=15):
            if url.endswith(".zip"):
                return _FakeResponse(200, zip_bytes)
            return _FakeResponse(404, b"")

    fake = _FakeSession()
    import requests
    monkeypatch.setattr(requests, "Session", lambda: fake)

    n = bc.download_bhavcopy(date(2018, 1, 15))
    assert n >= 1
    df = bc.get_ohlcv("TCS", days=3650)
    assert df is not None and len(df) == 1
    assert df.iloc[0]["close"] == 2010


def test_download_bhavcopy_skips_weekends(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    n = bc.download_bhavcopy(date(2026, 1, 17))  # a Saturday
    assert n == 0
