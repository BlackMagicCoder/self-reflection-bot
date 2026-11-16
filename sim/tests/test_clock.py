"""Unit tests for the virtual clock, SimCron, FakeScheduler, and the fake
datetime/date subclasses. Ported from the reference implementation's sim/test_clock.py
(pytest style, per this repo's conventions) plus coverage for the delta this
bot needs: a fake ``date`` (memory.py uses ``date.today()``, not ``datetime``)
and translating a REAL apscheduler CronTrigger passed into add_job (this
bot's per-user cron jobs are registered via real code paths the sim drives —
onboarding, /mood setup — not mirrored manually like the reference's main()).
"""

import asyncio

import pytest
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, timedelta

from sim.clock import FakeScheduler, SimCron, VirtualClock, make_fake_date, make_fake_datetime


class TestSimCron:
    def test_weekly(self):
        cron = SimCron(day_of_week="sat", hour=10, minute=0)
        nxt = cron.next_fire(datetime(2025, 12, 29, 8, 0))
        assert nxt == datetime(2026, 1, 3, 10, 0)
        assert cron.next_fire(nxt) == datetime(2026, 1, 10, 10, 0)

    def test_monthly_day(self):
        cron = SimCron(day=1, hour=10, minute=0)
        assert cron.next_fire(datetime(2026, 1, 1, 10, 0)) == datetime(2026, 2, 1, 10, 0)

    def test_hourly(self):
        cron = SimCron(minute=5)
        assert cron.next_fire(datetime(2026, 1, 1, 10, 5)) == datetime(2026, 1, 1, 11, 5)

    def test_from_apscheduler_weekday_cron(self):
        # bot._setup_weekly_reminder: CronTrigger(day_of_week=int, hour, minute)
        real = CronTrigger(day_of_week=2, hour=9, minute=30)  # Wed
        cron = SimCron.from_apscheduler(real)
        assert cron.day_of_week == "wed"
        assert cron.hour == 9
        assert cron.minute == 30
        assert cron.day is None

    def test_from_apscheduler_daily_cron(self):
        # bot._setup_mood_prompt / the two global daily jobs: no day_of_week.
        real = CronTrigger(hour=4, minute=0)
        cron = SimCron.from_apscheduler(real)
        assert cron.day_of_week is None
        assert cron.hour == 4
        assert cron.minute == 0


class TestFakeScheduler:
    def test_fires_in_time_order_and_date_jobs_once(self):
        clock = VirtualClock(datetime(2026, 1, 1, 0, 0))
        sched = FakeScheduler(clock)
        seen = []

        async def job(name):
            seen.append((name, clock.now()))

        sched.add_job(job, SimCron(minute=0), args=["hourly"], id="hourly")
        sched.add_job(job, trigger="date",
                      run_date=datetime(2026, 1, 1, 1, 30),
                      args=["oneshot"], id="oneshot")
        asyncio.run(sched.advance_to(datetime(2026, 1, 1, 3, 0)))
        assert [s[0] for s in seen] == ["hourly", "oneshot", "hourly", "hourly"]
        assert "oneshot" not in sched.jobs  # consumed
        assert clock.now() == datetime(2026, 1, 1, 3, 0)

    def test_replace_and_remove(self):
        clock = VirtualClock(datetime(2026, 1, 1, 0, 0))
        sched = FakeScheduler(clock)

        async def job():
            pass

        sched.add_job(job, trigger="date", run_date=clock.now() + timedelta(hours=1),
                      id="j", replace_existing=True)
        sched.add_job(job, trigger="date", run_date=clock.now() + timedelta(hours=2),
                      id="j", replace_existing=True)
        assert sched.jobs["j"].next_run == datetime(2026, 1, 1, 2, 0)
        sched.remove_job("j")
        with pytest.raises(KeyError):
            sched.remove_job("j")

    def test_accepts_real_cron_trigger_like_bot_setup_weekly_reminder(self):
        # Reproduces bot._setup_weekly_reminder's exact call shape.
        clock = VirtualClock(datetime(2026, 1, 1, 0, 0))  # Thursday
        sched = FakeScheduler(clock)
        fired = []

        async def send_reminder(chat_id):
            fired.append((chat_id, clock.now()))

        sched.add_job(send_reminder, CronTrigger(day_of_week=5, hour=10, minute=0),
                      args=[42], id="weekly_reminder_42", replace_existing=True)
        asyncio.run(sched.advance_to(datetime(2026, 1, 4, 0, 0)))  # next Saturday
        assert fired == [(42, datetime(2026, 1, 3, 10, 0))]


class TestFakeDateTimeAndDate:
    def test_datetime_subclass_behaves_like_datetime(self):
        clock = VirtualClock(datetime(2026, 1, 3, 10, 30))
        fdt = make_fake_datetime(clock)
        now = fdt.now()
        assert now.isocalendar()[:2] == (2026, 1)
        assert (now + timedelta(days=1)).day == 4
        assert fdt.strptime("2026-01-03 10:30", "%Y-%m-%d %H:%M") == datetime(2026, 1, 3, 10, 30)
        assert isinstance(now, datetime)

    def test_date_subclass_today_tracks_clock(self):
        clock = VirtualClock(datetime(2026, 1, 3, 23, 59))
        fd = make_fake_date(clock)
        assert fd.today() == fd(2026, 1, 3)
        clock.set(datetime(2026, 1, 4, 0, 1))
        assert fd.today() == fd(2026, 1, 4)
        # arithmetic and comparisons against real date objects still work
        from datetime import date as real_date
        assert (fd.today() - real_date(2026, 1, 1)).days == 3
