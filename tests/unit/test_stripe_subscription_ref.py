"""Unit tests for StripeWebhookService._subscription_ref (H8).

Verifies the metadata-snapshot/subscription-id extraction works on BOTH invoice
schemas — the legacy top-level fields and the 2025-03-31+ schema where they live
under invoice.parent.subscription_details — so the webhook avoids a blocking
Stripe API call whenever the snapshot carries the user_id. The invoice object is
dict-like, so plain dicts stand in for stripe.Invoice here.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.services.stripe_webhook_service import StripeWebhookService

ref = StripeWebhookService._subscription_ref


def test_legacy_top_level_schema():
    inv = {"subscription": "sub_1", "subscription_details": {"metadata": {"user_id": "7"}}}
    metadata, sub_id = ref(inv)
    assert metadata == {"user_id": "7"}
    assert sub_id == "sub_1"


def test_parent_schema_2025():
    inv = {"parent": {"subscription_details": {"subscription": "sub_2", "metadata": {"user_id": "9"}}}}
    metadata, sub_id = ref(inv)
    assert metadata == {"user_id": "9"}
    assert sub_id == "sub_2"


def test_no_subscription_returns_empty():
    inv = {"metadata": {"user_id": "1"}}
    metadata, sub_id = ref(inv)
    assert metadata == {}
    assert sub_id is None


def test_subscription_without_metadata_snapshot():
    # subscription present but snapshot empty -> caller will fall back to the API
    inv = {"subscription": "sub_3", "subscription_details": {}}
    metadata, sub_id = ref(inv)
    assert metadata == {}
    assert sub_id == "sub_3"


def test_parent_present_but_empty_is_safe():
    inv = {"parent": {}}
    metadata, sub_id = ref(inv)
    assert metadata == {}
    assert sub_id is None
