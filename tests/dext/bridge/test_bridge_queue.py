from datetime import timezone

from dext.bridge.queue import (
    FetchJob, FetchQueue, JobContext, JobStatus, new_job_id, utcnow,
)


def _job(url="https://x/1", identity_url=None):
    return FetchJob(id=new_job_id(), url=url, context=JobContext(),
                    created_at=utcnow(), timeout_seconds=60, identity_url=identity_url)


def test_take_next_is_fifo_and_marks_assigned():
    q = FetchQueue()
    a, b = _job("u1"), _job("u2")
    q.enqueue(a); q.enqueue(b)
    first = q.take_next()
    assert first is a and first.status is JobStatus.assigned
    assert q.assigned is a


def test_single_in_flight_blocks_second_take():
    q = FetchQueue()
    q.enqueue(_job("u1")); q.enqueue(_job("u2"))
    assert q.take_next() is not None
    assert q.take_next() is None  # one already assigned


def test_take_next_empty_returns_none():
    assert FetchQueue().take_next() is None


def test_find_matches_assigned_only():
    q = FetchQueue()
    j = _job(); q.enqueue(j); q.take_next()
    assert q.find(j.id) is j
    assert q.find("nope") is None


def test_finish_clears_slot_and_counts():
    q = FetchQueue()
    j = _job(); q.enqueue(j); q.take_next()
    q.finish(j, JobStatus.completed)
    assert q.assigned is None
    s = q.stats()
    assert (s.completed, s.assigned, s.pending) == (1, 0, 0)


def test_counters_accumulate_across_jobs():
    q = FetchQueue()
    for status in (JobStatus.completed, JobStatus.failed, JobStatus.skipped, JobStatus.completed):
        j = _job(); q.enqueue(j); q.take_next(); q.finish(j, status)
    s = q.stats()
    assert (s.completed, s.failed, s.skipped) == (2, 1, 1)


def test_discard_removes_pending_job_and_counts_failed():
    q = FetchQueue()
    a, b = _job("u1"), _job("u2")
    q.enqueue(a); q.enqueue(b)
    q.discard(b)              # b never assigned
    assert q.stats().failed == 1
    assert q.take_next() is a
    assert q.take_next() is None   # b is gone, a in flight
    q.finish(a, JobStatus.completed)
    assert q.take_next() is None


def test_discard_clears_assigned_job():
    q = FetchQueue()
    j = _job(); q.enqueue(j); q.take_next()
    q.discard(j)
    assert q.assigned is None
    assert q.stats().failed == 1


def test_helpers_produce_unique_ids_and_aware_utc():
    assert new_job_id() != new_job_id()
    assert utcnow().tzinfo is timezone.utc
