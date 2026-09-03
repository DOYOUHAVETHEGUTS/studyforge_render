"""
Scheduled delivery.

This is an in-process background loop, started when the FastAPI app starts
(`python -m studyforge.cli serve`). It is NOT a system cron / always-on service --
if you close the app, scheduled sends stop until you reopen it. For delivery that
must happen even when the app isn't kept open, use the OS-level alternative instead:

    Windows Task Scheduler / cron -> `python -m studyforge.cli send`

at whatever cadence you want. The in-app scheduler below is the zero-setup option for
"the app is generally left running" use, configured entirely from Settings.
"""
import asyncio
from datetime import datetime

from . import config, pipeline

CHECK_INTERVAL_SECONDS = 60


def is_due(settings: dict, now: datetime) -> bool:
    """Pure function (easy to unit test) deciding whether a scheduled send should fire now."""
    cadence = settings.get("schedule_cadence", "off")
    if cadence == "off":
        return False

    try:
        hh, mm = (int(x) for x in settings.get("schedule_time", "09:00").split(":"))
    except (ValueError, AttributeError):
        return False
    target_today = now.replace(hour=hh, minute=mm, second=0, microsecond=0)

    last_run_str = settings.get("schedule_last_run", "")
    last_run = None
    if last_run_str:
        try:
            last_run = datetime.fromisoformat(last_run_str)
        except ValueError:
            last_run = None

    if cadence == "daily":
        if now < target_today:
            return False
        return last_run is None or last_run.date() < now.date()

    if cadence == "weekly":
        weekday = int(settings.get("schedule_weekday", 0))
        if now.weekday() != weekday or now < target_today:
            return False
        # Already gated to the correct weekday above, so "not already sent today" is
        # sufficient -- the next match of this weekday is naturally 7 days later.
        return last_run is None or last_run.date() < now.date()

    return False


async def run_scheduler(log=print):
    """Background task: checks every CHECK_INTERVAL_SECONDS whether a scheduled send is due."""
    while True:
        try:
            settings = config.load_settings()
            now = datetime.now()
            if is_due(settings, now):
                log(f"[scheduler] sending scheduled delivery ({settings.get('schedule_cadence')})")
                result = await asyncio.to_thread(pipeline.send_next, None, settings=settings, log=log)
                settings["schedule_last_run"] = now.isoformat()
                config.save_settings(settings)
                log(f"[scheduler] result: {result.get('status')}")
        except Exception as e:  # noqa: BLE001 -- never let the background loop die
            log(f"[scheduler] error: {e}")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
