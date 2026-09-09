"""The contact-detail scanner (§13, FR-305 CONTAINS_CONTACT_DETAILS).

The contact embargo (FR-411) is trivially bypassed in prose, so job and bid
free text is scanned and flagged to staff. The scan advises, it never blocks,
so a false positive costs a staff glance while a false negative costs the
embargo. These tests are written in that spirit: the positive cases must fire,
and ordinary Australian prose about budgets, dates and site addresses must
not.
"""

from __future__ import annotations

import pytest

from app.domain.scanning import contains_contact_details, find_contact_details

EMAILS = [
    "Reach me at tom.okafor@meridiancloud.com.au to discuss.",
    "Send the schedule to bids+wireless@example.org please.",
    "contact: A.Nguyen@southern-cross.net",
]

PHONES = [
    "Call 02 9555 1234 during business hours.",
    "Mobile 0412 345 678 if it is urgent.",
    "Ring +61 2 9555 1234 for the site contact.",
    "Direct line 0295551234.",
    "Try 03-9555-1234 after hours.",
    "+61412345678 is the delivery lead.",
]

URLS = [
    "Our capability statement is at https://meridiancloud.com.au/capability.",
    "See www.southerncrossdigital.com.au for references.",
    "Portfolio: http://example.com/work",
]

HANDLES = [
    "WhatsApp: easiest way to reach the delivery lead.",
    "Skype me and we can walk through the plan.",
]

CLEAN = [
    "We run Exchange 2016 on a single host at our Rozelle site and need to move "
    "40 mailboxes to Microsoft 365, including shared mailboxes.",
    "Three clinic sites in inner west Sydney are running end-of-life access "
    "points. Work must be done outside clinic hours.",
    "The budget is between $24,000 and $40,000 and the work starts on 12 October 2026.",
    "Staged mailbox migration with a hybrid configuration, cutover over two weekends.",
    "Transition over eight weeks from our SA operations centre, with a shadowing "
    "period and an agreed knowledge base handover.",
    "We expect roughly 120 clinical workstations, moving to Intune management.",
    "Level 3, 45 Marine Parade, Adelaide SA 5000.",
]


@pytest.mark.parametrize("text", EMAILS)
def test_an_email_address_is_found(text):
    assert contains_contact_details(text) is True
    assert "email address" in find_contact_details(text)


@pytest.mark.parametrize("text", PHONES)
def test_an_australian_phone_number_is_found_in_several_formats(text):
    assert contains_contact_details(text) is True
    assert "phone number" in find_contact_details(text)


@pytest.mark.parametrize("text", URLS)
def test_a_website_is_found(text):
    assert contains_contact_details(text) is True
    assert "website" in find_contact_details(text)


@pytest.mark.parametrize("text", HANDLES)
def test_a_messaging_handle_is_found(text):
    assert contains_contact_details(text) is True
    assert "messaging handle" in find_contact_details(text)


@pytest.mark.parametrize("text", CLEAN)
def test_ordinary_prose_does_not_fire(text):
    assert find_contact_details(text) == []
    assert contains_contact_details(text) is False


def test_several_fields_are_scanned_together():
    """A job is scanned across title, description and any note, because
    splitting the detail across fields is the obvious evasion."""
    found = find_contact_details(
        "Wireless refresh",
        "Site survey required.",
        "Questions to priya@bayside.example and 02 9555 1234.",
    )
    assert found == ["email address", "phone number"]


def test_each_kind_is_reported_once_however_many_times_it_appears():
    found = find_contact_details("a@b.com and c@d.com and e@f.com")
    assert found == ["email address"]


def test_nothing_at_all_is_safe_to_scan():
    assert find_contact_details() == []
    assert find_contact_details(None, "", None) == []
    assert contains_contact_details(None) is False


def test_a_mixed_message_reports_every_kind_it_finds():
    found = find_contact_details(
        "Email tom@example.com, call 0412 345 678, or see www.example.com."
    )
    assert set(found) == {"email address", "phone number", "website"}
