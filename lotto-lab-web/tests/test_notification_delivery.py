from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from notification_delivery import NotificationDelivery


def subscription(endpoint="https://push.example.test/one"):
    return {"endpoint": endpoint, "keys": {"p256dh": "p", "auth": "a"}}


def test_subscription_is_persistent_and_deduplicated(tmp_path):
    delivery = NotificationDelivery(tmp_path)
    for _ in range(10):
        assert delivery.upsert(subscription(), user_agent="test") == 1
    reopened = NotificationDelivery(tmp_path)
    assert reopened.status()["subscriber_count"] == 1
    record = reopened.subscriptions()[0]
    assert record["enabled"] is True
    assert record["failure_count"] == 0
    assert record["created_at"]


def test_policy_and_queue_dedup(tmp_path):
    delivery = NotificationDelivery(tmp_path)
    delivery.upsert(subscription())
    for _ in range(10):
        delivery.enqueue("event-1", "SHADOW_RECOMMENDED", "INFO", "Research review is ready")
    assert delivery.status()["queue_count"] == 1
    try:
        delivery.enqueue("bad", "SLEEP", "INFO", "not allowed")
    except ValueError:
        pass
    else:
        raise AssertionError("routine event must not be queued")


def test_provider_acceptance_is_not_client_receipt(tmp_path):
    delivery = NotificationDelivery(tmp_path)
    delivery.upsert(subscription())
    delivery.enqueue("validation-1", "TEST_NOTIFICATION", "INFO", "Validation", validation_only=True)
    result = delivery.dispatch(lambda _subscription, _payload: None)
    assert result == {"provider_accepted": 1, "failed": 0}
    assert delivery.status()["provider_accepted"] == 1
    assert delivery.status()["client_received"] == 0
    notification_id = __import__("json").loads(delivery.queue_path.read_text())[0]["notification_id"]
    delivery.receipt(notification_id, "CLIENT_RECEIVED")
    delivery.receipt(notification_id, "CLIENT_RECEIVED")
    assert delivery.status()["client_received"] == 1


def test_invalid_subscription_is_disabled(tmp_path):
    delivery = NotificationDelivery(tmp_path)
    delivery.upsert(subscription())
    delivery.enqueue("event-2", "INTEGRITY_FAILURE", "CRITICAL", "Integrity review required")

    class Response:
        status_code = 410

    class Gone(Exception):
        response = Response()

    def sender(_subscription, _payload):
        raise Gone()

    delivery.dispatch(sender)
    assert delivery.subscriptions()[0]["enabled"] is False
    assert delivery.status()["subscriber_count"] == 0
