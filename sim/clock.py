"""Virtual time + fake scheduler for the simulator.

Ported from the reference implementation's sim/clock.py — the mechanism is identical
(a datetime subclass whose now() reads a VirtualClock; a fake scheduler that
fires jobs when the clock advances). The only delta is *which* module-level
names get patched: this bot reads time through ``date`` AND ``datetime`` in
bot.py (``from datetime import date, datetime, ...``) and only ``date`` in
memory.py (``from datetime import date``) — no ``datetime`` usage there at
all, and none in llm_client.py (verified by grep; re-verify after any change
to those imports). So two fake classes are patched in three places:
``bot.datetime``, ``bot.date``, ``memory.date``.

The fake scheduler replaces ``bot.scheduler`` (a module-level AsyncIOScheduler).
bot.py registers per-user cron jobs at runtime (weekly_reminder_{chat_id} on
onboarding/settings, mood_prompt_{chat_id} on /mood setup) and one-shot date
jobs (inactivity 1h/24h, homework +3 days) — those come from the real bot code
once the harness drives the corresponding handlers. Only the two GLOBAL cron
jobs registered in ``post_init`` (daily_backup, daily_period_rollup_check),
which the simulator never calls, must be registered by the harness itself.
"""

from __future__ import annotations

import inspect
from datetime import date, datetime, timedelta

from apscheduler.triggers.cron import CronTrigger as _RealCronTrigger

_WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


class VirtualClock:
    def __init__(self, start: datetime):
        self._now = start

    def now(self) -> datetime:
        return self._now

    def set(self, dt: datetime) -> None:
        if dt < self._now:
            raise ValueError(f"clock cannot go backwards: {self._now} -> {dt}")
        self._now = dt


def make_fake_datetime(clock: VirtualClock):
    """A datetime subclass whose now() reads the virtual clock."""

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ARG003 — bot only calls naive now()
            n = clock.now()
            return cls(n.year, n.month, n.day, n.hour, n.minute, n.second,
                       n.microsecond)

    return FakeDateTime


def make_fake_date(clock: VirtualClock):
    """A date subclass whose today() reads the virtual clock's date part."""

    class FakeDate(date):
        @classmethod
        def today(cls):
            n = clock.now()
            return cls(n.year, n.month, n.day)

    return FakeDate


class SimCron:
    """Minimal cron trigger: single weekday/day-of-month + hour + minute.

    Covers exactly the shapes this bot uses (per-user weekly weekday cron,
    per-user daily mood-prompt cron, two global daily crons). Fields left
    None are wildcards.
    """

    def __init__(self, day_of_week: str | None = None, day: int | None = None,
                 hour: int | None = None, minute: int = 0):
        if day_of_week is not None and day_of_week not in _WEEKDAYS:
            raise ValueError(f"unsupported day_of_week {day_of_week!r}")
        self.day_of_week = day_of_week
        self.day = day
        self.hour = hour
        self.minute = minute

    def _matches(self, dt: datetime) -> bool:
        if dt.minute != self.minute:
            return False
        if self.hour is not None and dt.hour != self.hour:
            return False
        if self.day is not None and dt.day != self.day:
            return False
        if self.day_of_week is not None and dt.weekday() != _WEEKDAYS[self.day_of_week]:
            return False
        return True

    def next_fire(self, after: datetime) -> datetime:
        # Scan minute-by-minute; hard bound of 62 days covers every field combo.
        t = (after + timedelta(minutes=1)).replace(second=0, microsecond=0)
        for _ in range(62 * 24 * 60):
            if self._matches(t):
                return t
            t += timedelta(minutes=1)
        raise RuntimeError("SimCron found no fire time within 62 days")

    @classmethod
    def from_apscheduler(cls, trigger) -> "SimCron":
        """Translate a real apscheduler CronTrigger's fields into a SimCron.

        Covers exactly this bot's usage: day_of_week=int 0-6 or None,
        hour=int, minute=int (bot.py always passes concrete hour+minute).
        """
        fields = {f.name: f for f in trigger.fields}
        day_of_week = None
        dow_field = fields.get("day_of_week")
        if dow_field is not None and str(dow_field) != "*":
            day_of_week = [k for k, v in _WEEKDAYS.items() if v == int(str(dow_field))][0]
        day = None
        day_field = fields.get("day")
        if day_field is not None and str(day_field) != "*":
            day = int(str(day_field))
        hour = int(str(fields["hour"])) if str(fields.get("hour", "*")) != "*" else None
        minute = int(str(fields["minute"])) if str(fields.get("minute", "*")) != "*" else 0
        return cls(day_of_week=day_of_week, day=day, hour=hour, minute=minute)


class _Job:
    def __init__(self, job_id: str, func, args: list, next_run: datetime,
                 cron: SimCron | None):
        self.id = job_id
        self.func = func
        self.args = args
        self.next_run = next_run
        self.cron = cron  # None => one-shot date job


class FakeScheduler:
    """Records add_job/remove_job; fires due jobs when the clock advances."""

    def __init__(self, clock: VirtualClock):
        self._clock = clock
        self.jobs: dict[str, _Job] = {}
        self._seq = 0
        self.log: list[tuple[str, str, str]] = []  # (iso-time, action, job_id)

    # -- API surface used by bot.py ------------------------------------------
    def add_job(self, func, trigger=None, run_date=None, args=None, id=None,
                replace_existing=False, **_kw):
        job_id = id or f"job{self._seq}"
        self._seq += 1
        if job_id in self.jobs and not replace_existing:
            raise ValueError(f"job {job_id} already exists")
        if isinstance(trigger, SimCron):
            job = _Job(job_id, func, list(args or []),
                       trigger.next_fire(self._clock.now()), trigger)
        elif trigger == "date":
            if run_date is None:
                raise ValueError("date trigger needs run_date")
            job = _Job(job_id, func, list(args or []), run_date, None)
        elif isinstance(trigger, _RealCronTrigger):
            # This bot registers per-user cron jobs (weekly_reminder_{chat_id},
            # mood_prompt_{chat_id}) via real code paths the sim drives
            # (onboarding, /mood setup), passing a real apscheduler CronTrigger
            # — translate rather than reject.
            cron = SimCron.from_apscheduler(trigger)
            job = _Job(job_id, func, list(args or []),
                       cron.next_fire(self._clock.now()), cron)
        else:
            raise ValueError(f"FakeScheduler: unsupported trigger {trigger!r}")
        self.jobs[job_id] = job
        self.log.append((self._clock.now().isoformat(), "add", job_id))
        return job

    def remove_job(self, job_id: str) -> None:
        if job_id not in self.jobs:
            raise KeyError(job_id)  # bot.py wraps removals in try/except
        del self.jobs[job_id]
        self.log.append((self._clock.now().isoformat(), "remove", job_id))

    def start(self) -> None:
        """No-op: the fake scheduler is always 'running'; bot.py's post_init
        calls this once. Kept so harness code that mirrors post_init needs no
        special-casing."""

    # -- simulator side -------------------------------------------------------
    def _pop_earliest_due(self, until: datetime) -> _Job | None:
        due = [j for j in self.jobs.values() if j.next_run <= until]
        if not due:
            return None
        job = min(due, key=lambda j: (j.next_run, j.id))
        if job.cron is None:
            del self.jobs[job.id]
        return job

    async def advance_to(self, target: datetime) -> list[str]:
        """Advance the clock to target, firing every due job in time order.

        Returns the ids of the jobs that fired (in order), for assertions.
        """
        fired = []
        while True:
            job = self._pop_earliest_due(target)
            if job is None:
                break
            if job.next_run > self._clock.now():
                self._clock.set(job.next_run)
            fire_time = job.next_run
            if job.cron is not None:
                job.next_run = job.cron.next_fire(fire_time)
            self.log.append((fire_time.isoformat(), "fire", job.id))
            fired.append(job.id)
            result = job.func(*job.args)
            if inspect.isawaitable(result):
                await result
        if target > self._clock.now():
            self._clock.set(target)
        return fired
