import pytest
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import uuid

from scheduling_agent.core.date_utils import DateUtils
from scheduling_agent.db.models import Event, ExcludedDate

@pytest.fixture
def date_utils() -> DateUtils:
    return DateUtils()

def test_parse_datetime_utc(date_utils: DateUtils):
    """Test parsing a datetime string with Z (UTC) timezone."""
    dt_str = "2024-01-01T10:30:00Z"
    parsed_dt = date_utils.parse_datetime(dt_str)
    assert parsed_dt == datetime(2024, 1, 1, 10, 30, 0, tzinfo=timezone.utc)
    assert parsed_dt.tzinfo == timezone.utc

def test_parse_datetime_offset(date_utils: DateUtils):
    """Test parsing a datetime string with a specific offset."""
    dt_str = "2024-01-01T10:30:00-05:00"
    parsed_dt = date_utils.parse_datetime(dt_str)
    expected_utc = datetime(2024, 1, 1, 15, 30, 0, tzinfo=timezone.utc)
    # Check both the original interpretation and UTC conversion
    assert parsed_dt.year == 2024
    assert parsed_dt == expected_utc
    assert parsed_dt.tzinfo == timezone.utc

def test_parse_datetime_naive_assumes_utc(date_utils: DateUtils):
    """Test parsing a naive datetime string assumes UTC."""
    dt_str = "2024-01-01T10:30:00"
    parsed_dt = date_utils.parse_datetime(dt_str)
    assert parsed_dt == datetime(2024, 1, 1, 10, 30, 0, tzinfo=timezone.utc)
    assert parsed_dt.tzinfo == timezone.utc

def test_parse_datetime_invalid(date_utils: DateUtils):
    """Test parsing an invalid datetime string raises ValueError."""
    dt_str = "invalid-date-string"
    with pytest.raises(ValueError):
        date_utils.parse_datetime(dt_str)

def test_normalize_to_utc_aware(date_utils: DateUtils):
    """Test normalizing an already timezone-aware datetime to UTC."""
    dt_aware = datetime(2024, 1, 1, 10, 0, 0, tzinfo=ZoneInfo("America/New_York"))
    dt_utc = date_utils.normalize_to_utc(dt_aware)
    assert dt_utc == datetime(2024, 1, 1, 15, 0, 0, tzinfo=timezone.utc)
    assert dt_utc.tzinfo == timezone.utc

def test_normalize_to_utc_naive_with_tz(date_utils: DateUtils):
    """Test normalizing a naive datetime, assuming a specific timezone."""
    dt_naive = datetime(2024, 1, 1, 10, 0, 0)
    dt_utc = date_utils.normalize_to_utc(dt_naive, tz_name="America/New_York")
    assert dt_utc == datetime(2024, 1, 1, 15, 0, 0, tzinfo=timezone.utc)
    assert dt_utc.tzinfo == timezone.utc

def test_normalize_to_utc_naive_no_tz(date_utils: DateUtils):
    """Test normalizing a naive datetime, assuming UTC if no tz provided."""
    dt_naive = datetime(2024, 1, 1, 10, 0, 0)
    dt_utc = date_utils.normalize_to_utc(dt_naive) # No tz_name provided
    assert dt_utc == datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    assert dt_utc.tzinfo == timezone.utc

def test_convert_from_utc(date_utils: DateUtils):
    """Test converting a UTC datetime to a specific timezone."""
    dt_utc = datetime(2024, 1, 1, 15, 0, 0, tzinfo=timezone.utc)
    dt_local = date_utils.convert_from_utc(dt_utc, tz_name="America/New_York")
    assert dt_local.year == 2024
    assert dt_local.month == 1
    assert dt_local.day == 1
    assert dt_local.hour == 10
    assert dt_local.minute == 0
    assert dt_local.tzinfo == ZoneInfo("America/New_York")

def test_convert_from_utc_naive_input(date_utils: DateUtils):
    """Test converting a naive datetime (assumed UTC) to a specific timezone."""
    dt_naive_utc = datetime(2024, 1, 1, 15, 0, 0)
    dt_local = date_utils.convert_from_utc(dt_naive_utc, tz_name="America/New_York")
    assert dt_local.hour == 10
    assert dt_local.tzinfo == ZoneInfo("America/New_York")

def test_convert_from_utc_invalid_tz(date_utils: DateUtils):
    """Test conversion raises error for invalid timezone name."""
    dt_utc = datetime(2024, 1, 1, 15, 0, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        date_utils.convert_from_utc(dt_utc, tz_name="Invalid/Timezone")

# --- Recurrence Expansion Tests ---

def test_expand_recurrence_simple_weekly(date_utils: DateUtils):
    """Test expanding a simple weekly recurrence rule."""
    # Event: Weekly on Monday at 9am UTC for 5 weeks
    start_dt = datetime(2024, 5, 6, 9, 0, 0, tzinfo=timezone.utc) # First Monday in May
    end_dt = datetime(2024, 5, 6, 10, 0, 0, tzinfo=timezone.utc)
    rrule_str = "FREQ=WEEKLY;BYDAY=MO;COUNT=5"

    base_event_data = {
        "id": uuid.uuid4(),
        "title": "Weekly Meeting",
        "start_time": start_dt.replace(tzinfo=None), # Store naive UTC
        "end_time": end_dt.replace(tzinfo=None),
        "time_zone": "UTC",
        "rrule": rrule_str
        # No excluded_dates or modified_instances needed for this test
    }
    # Use model_validate for creating test instance from dict
    # Need to import Event from db models for this
    from scheduling_agent.db.models import Event
    base_event = Event.model_validate(base_event_data)

    # Define range for expansion
    range_start = datetime(2024, 5, 1, 0, 0, 0, tzinfo=timezone.utc)
    range_end = datetime(2024, 6, 10, 0, 0, 0, tzinfo=timezone.utc)

    # --- Act ---
    # Assume expand_recurrence takes base event, range, and optional exceptions
    # For this test, exceptions are empty lists
    # Let's assume expand_recurrence returns a list of simple dicts or EventRead models
    # for now, not full Event DB models, to represent the expanded occurrences.
    occurrences = date_utils.expand_recurrence(
        base_event=base_event,
        range_start_utc=range_start,
        range_end_utc=range_end,
        excluded_dates=[],
        modified_instances=[]
    )

    # --- Assert ---
    assert len(occurrences) == 5 # COUNT=5

    expected_dates = [
        datetime(2024, 5, 6, 9, 0, 0, tzinfo=timezone.utc),
        datetime(2024, 5, 13, 9, 0, 0, tzinfo=timezone.utc),
        datetime(2024, 5, 20, 9, 0, 0, tzinfo=timezone.utc),
        datetime(2024, 5, 27, 9, 0, 0, tzinfo=timezone.utc),
        datetime(2024, 6, 3, 9, 0, 0, tzinfo=timezone.utc),
    ]

    # Check the start times of the generated occurrences (should be timezone aware UTC)
    # Assuming the returned occurrence object has a 'start_time' attribute
    occurrence_start_times = sorted([occ['start_time'] for occ in occurrences])
    assert occurrence_start_times == expected_dates

    # Check properties of the first occurrence
    first_occurrence = occurrences[0]
    assert first_occurrence['title'] == base_event.title
    assert first_occurrence['start_time'] == expected_dates[0]
    assert first_occurrence['end_time'] == expected_dates[0] + (end_dt - start_dt)
    assert first_occurrence['time_zone'] == base_event.time_zone
    # Check specific instance details
    assert first_occurrence['rrule'] is None # Occurrences don't have rrule
    assert first_occurrence['base_event_id'] == base_event.id # Link back to base
    assert first_occurrence['instance_start_time'] == expected_dates[0] # Identify this specific instance time

def test_expand_recurrence_with_exclusion(date_utils: DateUtils):
    """Test expanding recurrence while excluding specific dates."""
    # Arrange: Daily event for 1 week, exclude the 3rd day
    start_dt = datetime(2024, 7, 1, 9, 0, 0, tzinfo=timezone.utc)
    end_dt = datetime(2024, 7, 1, 10, 0, 0, tzinfo=timezone.utc)
    rrule_str = "FREQ=DAILY;COUNT=7"
    base_event = Event.model_validate({
        "id": uuid.uuid4(), "title": "Daily Task",
        "start_time": start_dt.replace(tzinfo=None),
        "end_time": end_dt.replace(tzinfo=None),
        "time_zone": "UTC", "rrule": rrule_str
    })

    # Exclude July 3rd (stored as naive datetime at midnight UTC)
    exclusion_dt = datetime(2024, 7, 3, 0, 0, 0)
    excluded = [ExcludedDate(event_id=base_event.id, excluded_date=exclusion_dt)]

    range_start = datetime(2024, 7, 1, 0, 0, 0, tzinfo=timezone.utc)
    range_end = datetime(2024, 7, 10, 0, 0, 0, tzinfo=timezone.utc)

    # Act
    occurrences = date_utils.expand_recurrence(
        base_event=base_event,
        range_start_utc=range_start,
        range_end_utc=range_end,
        excluded_dates=excluded,
        modified_instances=[]
    )

    # Assert
    assert len(occurrences) == 6 # 7 total minus 1 excluded
    occurrence_starts = {occ['start_time'].date() for occ in occurrences}
    assert datetime(2024, 7, 1).date() in occurrence_starts
    assert datetime(2024, 7, 2).date() in occurrence_starts
    assert datetime(2024, 7, 3).date() not in occurrence_starts # Check exclusion
    assert datetime(2024, 7, 4).date() in occurrence_starts
    assert datetime(2024, 7, 5).date() in occurrence_starts
    assert datetime(2024, 7, 6).date() in occurrence_starts
    assert datetime(2024, 7, 7).date() in occurrence_starts

def test_expand_recurrence_with_modification(date_utils: DateUtils):
    """Test expanding recurrence with a modified instance."""
    # Arrange: Weekly event, modify the 2nd instance
    start_dt = datetime(2024, 5, 6, 9, 0, 0, tzinfo=timezone.utc)
    end_dt = datetime(2024, 5, 6, 10, 0, 0, tzinfo=timezone.utc)
    rrule_str = "FREQ=WEEKLY;BYDAY=MO;COUNT=4"
    base_event = Event.model_validate({
        "id": uuid.uuid4(), "title": "Weekly Base",
        "start_time": start_dt.replace(tzinfo=None),
        "end_time": end_dt.replace(tzinfo=None),
        "time_zone": "UTC", "rrule": rrule_str, "location": "Office"
    })

    # Modify the second instance (May 13th)
    original_instance_date = datetime(2024, 5, 13, 0, 0, 0) # Naive UTC date
    mod_start_dt = datetime(2024, 5, 13, 9, 30, 0) # Naive UTC time
    mod_end_dt = datetime(2024, 5, 13, 10, 30, 0)
    mod_title = "Modified Weekly"
    mod_location = "Virtual"

    # Need ModifiedInstance model
    from scheduling_agent.db.models import ModifiedInstance
    modified = [ModifiedInstance(
        event_id=base_event.id,
        original_date=original_instance_date,
        start_time=mod_start_dt,
        end_time=mod_end_dt,
        time_zone="UTC", # Specify TZ for the modification
        title=mod_title,
        location=mod_location
    )]

    range_start = datetime(2024, 5, 1, 0, 0, 0, tzinfo=timezone.utc)
    range_end = datetime(2024, 6, 1, 0, 0, 0, tzinfo=timezone.utc)

    # Act
    occurrences = date_utils.expand_recurrence(
        base_event=base_event,
        range_start_utc=range_start,
        range_end_utc=range_end,
        excluded_dates=[],
        modified_instances=modified
    )

    # Assert
    assert len(occurrences) == 4
    occurrence_starts = {occ['start_time'] for occ in occurrences}

    # Check the modified instance (May 13th)
    mod_instance_data = next((occ for occ in occurrences if occ['start_time'].date() == datetime(2024, 5, 13).date()), None)
    assert mod_instance_data is not None
    assert mod_instance_data['title'] == mod_title
    assert mod_instance_data['location'] == mod_location
    # Start time should be the modified one, normalized to aware UTC
    assert mod_instance_data['start_time'] == mod_start_dt.replace(tzinfo=timezone.utc)
    assert mod_instance_data['end_time'] == mod_end_dt.replace(tzinfo=timezone.utc)
    assert mod_instance_data['modified_instance_id'] is not None # Should have the mod ID

    # Check a non-modified instance (May 6th)
    non_mod_instance_data = next((occ for occ in occurrences if occ['start_time'].date() == datetime(2024, 5, 6).date()), None)
    assert non_mod_instance_data is not None
    assert non_mod_instance_data['title'] == base_event.title
    assert non_mod_instance_data['location'] == base_event.location
    assert non_mod_instance_data['start_time'] == start_dt # Original start time
    assert non_mod_instance_data['modified_instance_id'] is None


# TODO: Add tests for expand_recurrence with:
# - Modifications (ModifiedInstance) -> Added test

# TODO: Add tests for normalize_to_utc, convert_from_utc, expand_recurrence etc. 