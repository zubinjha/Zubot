from src.zubot.core.worker_capacity_policy import can_dispatch_task_agent_worker


def test_can_dispatch_when_under_usable_capacity():
    assert can_dispatch_task_agent_worker(running_count=0, max_concurrent_workers=3, reserve_for_workers=2) is True


def test_blocks_when_at_or_over_reserved_boundary():
    assert can_dispatch_task_agent_worker(running_count=1, max_concurrent_workers=3, reserve_for_workers=2) is False
    assert can_dispatch_task_agent_worker(running_count=2, max_concurrent_workers=3, reserve_for_workers=2) is False


def test_handles_zero_or_negative_limits_safely():
    assert can_dispatch_task_agent_worker(running_count=0, max_concurrent_workers=0, reserve_for_workers=2) is False
    assert can_dispatch_task_agent_worker(running_count=0, max_concurrent_workers=-1, reserve_for_workers=2) is False


def test_negative_reserve_treated_as_zero():
    assert can_dispatch_task_agent_worker(running_count=2, max_concurrent_workers=3, reserve_for_workers=-5) is True
