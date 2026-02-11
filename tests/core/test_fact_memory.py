from src.zubot.core.fact_memory import extract_facts_from_events, extract_facts_from_text


def test_extract_facts_from_text_name_and_location():
    facts = extract_facts_from_text("My name is Zubin Jha and I live in Worthington, Ohio.")
    assert facts["user_name"] == "Zubin Jha"
    assert facts["home_location"] == "Worthington, Ohio"


def test_extract_facts_from_events_merges_existing_facts():
    existing = {"timezone": "America/New_York"}
    events = [{"event_type": "user_message", "payload": {"text": "Call me Zubin"}}]
    facts = extract_facts_from_events(events, existing_facts=existing)
    assert facts["timezone"] == "America/New_York"
    assert facts["preferred_name"] == "Zubin"
