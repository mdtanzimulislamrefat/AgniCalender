"""Refresh the NASA rolling feeds in the background while the API server runs."""
import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

from backend.processing.recent import refresh_all

log = logging.getLogger('uvicorn.error')
MIN_INTERVAL = timedelta(minutes=15)  # NASA updates NRT feeds within ~3 h of each pass
RETRY_AFTER = timedelta(minutes=15)


def parse_interval(text):
    """'3h', '30m', '1d' -> timedelta; 'off' -> None."""
    if text == 'off':
        return None
    match = re.fullmatch(r'(\d+)([mhd])', text)
    if not match:
        raise ValueError("Use a number with m, h or d (e.g. 3h), or 'off'")
    unit = {'m': 'minutes', 'h': 'hours', 'd': 'days'}[match[2]]
    interval = timedelta(**{unit: int(match[1])})
    if interval < MIN_INTERVAL:
        raise ValueError('Fetch at most every 15 minutes to respect NASA servers')
    return interval


def now():
    return datetime.now(timezone.utc)


class FetchScheduler:
    """Runs one fetch at a time: at startup, then every interval (sooner after a failure)."""

    def __init__(self, interval, fetch=refresh_all):
        self.interval, self.fetch = interval, fetch
        self.status = {'enabled': interval is not None,
                       'interval_minutes': int(interval.total_seconds() // 60) if interval else None,
                       'last_run_utc': None, 'last_result': None, 'recent_result': None, 'next_run_utc': None}

    async def run_once(self):
        started = now()
        try:
            outcome = await asyncio.to_thread(self.fetch)
        except Exception as exc:  # the loop must survive NASA, network or database outages
            log.warning('NASA fetch failed (%s); the app keeps serving the previous report', type(exc).__name__)
            result = 'failed'
        else:
            result = {'new': 'new_report', 'unchanged': 'unchanged'}.get(outcome['status'], 'failed')
            detail = f" #{outcome['report_id']}, {outcome['counts']['retained']} hotspots" if result == 'new_report' else ''
            recent = outcome.get('recent', {}).get('status')
            self.status['recent_result'] = recent
            log.info('NASA fetch: %s%s; recent days: %s', result, detail, recent)
        self.status.update(last_run_utc=started.isoformat(), last_result=result)
        return result != 'failed'

    async def loop(self):
        while True:
            ok = await self.run_once()
            delay = self.interval if ok else min(self.interval, RETRY_AFTER)
            self.status['next_run_utc'] = (now() + delay).isoformat()
            await asyncio.sleep(delay.total_seconds())
