from datetime import datetime, timezone

from data.nws_climate_report_client import _extract_product_text, _parse_time
from data.nws_climate_report_parser import parse_cli_report


def test_parse_time_handles_iso_timestamps():
    """Regression: previously _parse_time always returned None because the body
    that converted ISO strings had been misplaced into _extract_product_text as
    unreachable code. issued_at metadata was therefore lost for every report."""
    parsed = _parse_time("2026-04-30T08:51:00Z")
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.astimezone(timezone.utc) == datetime(2026, 4, 30, 8, 51, tzinfo=timezone.utc)
    assert _parse_time(None) is None
    assert _parse_time("") is None


def test_extract_product_text_returns_raw_text_when_no_pre_block():
    raw = "RAW PLAIN TEXT REPORT CONTENT"
    assert _extract_product_text(raw) == raw
    html_wrapped = '<html><pre class="glossaryProduct">  HELLO\nWORLD </pre></html>'
    assert "HELLO" in _extract_product_text(html_wrapped)


def test_parse_cli_daily_high_low_precip():
    raw = """
CLIMATE REPORT
NATIONAL WEATHER SERVICE PEACHTREE CITY GA
420 AM EDT THU APR 16 2026

...THE ATLANTA CLIMATE SUMMARY FOR APRIL 15 2026...

TEMPERATURE (F)
 YESTERDAY
  MAXIMUM         85   3:51 PM
  MINIMUM         60   6:44 AM

PRECIPITATION (IN)
  YESTERDAY        0.00
"""
    parsed = parse_cli_report(raw)
    assert parsed.report_date.isoformat() == "2026-04-15"
    assert parsed.high_temp == 85
    assert parsed.low_temp == 60
    assert parsed.precip == 0
    assert parsed.confidence >= 0.9
