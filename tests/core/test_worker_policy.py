from src.zubot.core.worker_policy import should_forward_worker_event_to_user


def test_should_forward_worker_event_to_user_always_true_v1():
    assert should_forward_worker_event_to_user({"type": "worker_completed"}, {}) is True
    assert should_forward_worker_event_to_user({"type": "worker_blocked"}, None) is True
