"""Date and time utility functions."""

from datetime import UTC, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import re # Import re

from dateutil import rrule
from dateutil.parser import parse as dateutil_parse

from scheduling_agent.db.models import Event, ExcludedDate, ModifiedInstance
from scheduling_agent.logging_config import get_logger

import dateparser

logger = get_logger(__name__)


class DateUtils:
    """Provides utilities for handling dates, times, timezones, and recurrence."""

    def parse_datetime(self, dt_str: str) -> datetime:
        """Parses a datetime string into a timezone-aware datetime object.

        Handles ISO 8601 formats, including those with 'Z' or offsets.
        If no timezone info is present, assumes UTC.

        Args:
            dt_str: The datetime string to parse.

        Returns:
            A timezone-aware datetime object.

        Raises:
            ValueError: If the string cannot be parsed.
        """
        try:
            dt = dateutil_parse(dt_str)

            # If parsing resulted in a naive datetime, assume UTC
            if dt.tzinfo is None:
                return dt.replace(tzinfo=UTC)

            # If it has timezone info, normalize it to standard library UTC
            return dt.astimezone(UTC)
        except Exception as e:
            logger.exception(f"Failed to parse datetime string: '{dt_str}'", error=str(e))
            raise ValueError(f"Invalid datetime format: {dt_str}") from e

    def normalize_to_utc(self, dt: datetime, tz_name: str | None = None) -> datetime:
        """Converts a datetime (aware or naive) to an aware UTC datetime.

        If the input datetime is naive, it's assumed to be in the timezone
        specified by tz_name (or UTC if tz_name is None) before conversion.

        Args:
            dt: The input datetime.
            tz_name: The IANA timezone name if dt is naive.

        Returns:
            An aware datetime object normalized to UTC.
        """
        if dt.tzinfo is None:
            # Input is naive, assume tz_name or UTC
            local_tz_name = tz_name or "UTC"
            try:
                local_tz = ZoneInfo(local_tz_name)
                dt = dt.replace(tzinfo=local_tz)
            except ZoneInfoNotFoundError:
                logger.warning(
                    f"Invalid timezone '{local_tz_name}' provided for naive datetime, assuming UTC."
                )
                dt = dt.replace(tzinfo=UTC)
            except Exception:
                logger.exception("Error applying timezone to naive datetime")
                # Fallback to assuming UTC
                dt = dt.replace(tzinfo=UTC)

        # Now dt is guaranteed to be aware, convert to UTC
        return dt.astimezone(UTC)

    def convert_from_utc(self, dt_utc: datetime, tz_name: str) -> datetime:
        """Converts an aware UTC datetime to the specified target timezone.

        If the input datetime is naive, it's assumed to be UTC.

        Args:
            dt_utc: The aware UTC datetime object (or naive assumed UTC).
            tz_name: The target IANA timezone name.

        Returns:
            An aware datetime object in the target timezone.

        Raises:
            ValueError: If the target timezone name is invalid.
        """
        if dt_utc.tzinfo is None:
            # Assume naive input is UTC
            dt_utc = dt_utc.replace(tzinfo=UTC)

        try:
            target_tz = ZoneInfo(tz_name)
            return dt_utc.astimezone(target_tz)
        except ZoneInfoNotFoundError as e:
            logger.exception(f"Invalid target timezone name: '{tz_name}'")
            raise ValueError(f"Invalid target timezone: {tz_name}") from e
        except Exception as e:
            logger.exception("Error converting timezone")
            raise ValueError(f"Could not convert to timezone: {tz_name}") from e

    @staticmethod
    @lru_cache(maxsize=128)
    def _parse_rrule_string(rrule_str: str, dtstart: datetime) -> rrule.rruleset | None:
        """Parses an RRULE string into a dateutil rruleset, with caching."""
        if not rrule_str:
            return None
        try:
            # dateutil.rrule.rrulestr requires aware dtstart
            if dtstart.tzinfo is None:
                dtstart = dtstart.replace(tzinfo=UTC)

            # Fix for UNTIL date-only values when dtstart is aware
            # dateutil requires UNTIL to be UTC if dtstart is aware.
            def _fix_until(match):
                until_val = match.group(1)
                if len(until_val) == 8 and until_val.isdigit():
                    # It's a date YYYYMMDD, convert to YYYYMMDDT235959Z
                    return f"UNTIL={until_val}T235959Z"
                # Otherwise, return the original UNTIL part (already datetime or invalid)
                return match.group(0)

            # Use regex to find and potentially replace UNTIL=YYYYMMDD
            # Ensure we only match UNTIL= specifically, not other keys containing UNTIL
            # Match UNTIL= followed by digits or a full datetime string until ; or end
            rrule_str_fixed = re.sub(r"\bUNTIL=([0-9TZ]+)\b", _fix_until, rrule_str, count=1)

            # TODO: Handle potential EXDATE/RDATE within the rrule_str itself if needed
            # Currently assuming rrule_str is just the main RRULE part.
            rule = rrule.rrulestr(rrule_str_fixed, dtstart=dtstart, forceset=True, cache=True)
            return rule
        except Exception as e:
            logger.exception(f"Failed to parse RRULE string: '{rrule_str}' (fixed: '{rrule_str_fixed}')", error=str(e))
            return None  # Return None or raise an error?

    def parse_nlp_date_range(
        self,
        start_date_str: str | None,
        end_date_str: str | None,
        default_start: str = "today",
        default_end: str = "next Sunday",
    ) -> tuple[datetime, datetime]:
        """Parses start/end date strings (often from NLP) into an aware UTC date range.

        Applies defaults, adjusts times to cover full days, and handles parsing errors.

        Args:
            start_date_str: The start date string (e.g., "tomorrow", "2024-05-10").
            end_date_str: The end date string.
            default_start: Default string if start_date_str is None or empty.
            default_end: Default string if end_date_str is None or empty.

        Returns:
            A tuple containing (range_start_utc, range_end_utc), both aware UTC datetimes.
        """
        start_str_to_parse = start_date_str or default_start
        end_str_to_parse = end_date_str or default_end

        range_start_utc: datetime | None = None
        range_end_utc: datetime | None = None

        # Use dateparser for flexible string parsing
        parser_settings = {"RETURN_AS_TIMEZONE_AWARE": True, "TIMEZONE": "UTC"}

        try:
            # Parse start date string using dateparser
            start_dt = dateparser.parse(start_str_to_parse, settings=parser_settings)
            if start_dt:
                # Set time to beginning of the day
                start_dt = start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
                # Ensure it's aware UTC (dateparser should handle this with settings)
                range_start_utc = self.normalize_to_utc(start_dt)

            # Parse end date string using dateparser
            end_dt = dateparser.parse(end_str_to_parse, settings=parser_settings)
            if end_dt:
                # Set time to end of the day
                end_dt = end_dt.replace(hour=23, minute=59, second=59, microsecond=999999)
                # Ensure it's aware UTC
                range_end_utc = self.normalize_to_utc(end_dt)

            # Handle parsing failures with defaults
            if range_start_utc is None:
                logger.warning(
                    f"Failed to parse start date '{start_str_to_parse}', defaulting to today."
                )
                range_start_utc = self.normalize_to_utc(
                    datetime.combine(datetime.now(UTC).date(), time.min) # Aware UTC start of today
                )
            if range_end_utc is None:
                logger.warning(
                    f"Failed to parse end date '{end_str_to_parse}', defaulting to 7 days from start."
                )
                # Default end is 7 days from the *determined* start date
                range_end_utc = range_start_utc + timedelta(days=7)
                # Ensure end time is end of day
                range_end_utc = range_end_utc.replace(hour=23, minute=59, second=59, microsecond=999999)

            # Ensure start is before end
            if range_start_utc >= range_end_utc:
                logger.warning(
                    f"Parsed start date '{start_str_to_parse}' is on or after end date '{end_str_to_parse}', adjusting end date."
                )
                range_end_utc = range_start_utc + timedelta(days=7)
                range_end_utc = range_end_utc.replace(hour=23, minute=59, second=59, microsecond=999999)

        except Exception as parse_err:
            # Fallback completely if any unexpected error occurs during parsing
            logger.exception(
                f"Unexpected error parsing date range ('{start_str_to_parse}' - '{end_str_to_parse}'), defaulting to next 7 days.",
                error=parse_err,
            )
            range_start_utc = self.normalize_to_utc(
                 datetime.combine(datetime.now(UTC).date(), time.min)
            )
            range_end_utc = range_start_utc + timedelta(days=7)
            range_end_utc = range_end_utc.replace(hour=23, minute=59, second=59, microsecond=999999)

        return range_start_utc, range_end_utc

    def expand_recurrence(
        self,
        base_event: Event,
        range_start_utc: datetime,
        range_end_utc: datetime,
        excluded_dates: list[ExcludedDate],
        modified_instances: list[ModifiedInstance],
    ) -> list[dict]:  # Returning list of dicts for now
        """Expands a recurring event within a date range, applying exclusions/modifications.

        Args:
            base_event: The base recurring Event object.
            range_start_utc: The start of the range (aware UTC datetime).
            range_end_utc: The end of the range (aware UTC datetime).
            excluded_dates: List of ExcludedDate objects for this event.
            modified_instances: List of ModifiedInstance objects for this event.

        Returns:
            A list of dictionaries, each representing an event occurrence.
        """
        occurrences = []
        if not base_event.rrule:
            return occurrences  # Not a recurring event

        # Base event times are stored as NAIVE UTC.
        base_start_naive_utc = base_event.start_time
        base_end_naive_utc = base_event.end_time
        original_tz_name = base_event.time_zone

        # Calculate duration based on naive UTC times (consistent)
        duration = base_end_naive_utc - base_start_naive_utc

        # Convert the naive UTC start time to the ORIGINAL LOCAL timezone aware time for rrule
        try:
            original_local_dtstart = self.convert_from_utc(
                base_start_naive_utc.replace(tzinfo=UTC), original_tz_name
            )
        except ValueError:
            logger.exception(
                f"Invalid original timezone '{original_tz_name}' for event {base_event.id}, cannot expand recurrence accurately."
            )
            return occurrences  # Cannot proceed without valid original TZ

        # Parse the RRULE string using the AWARE LOCAL start time
        ruleset = DateUtils._parse_rrule_string(base_event.rrule, dtstart=original_local_dtstart)
        if not ruleset:
            return occurrences  # Could not parse rule

        # Prepare lookup sets/dicts for exclusions and modifications
        excluded_set = {ex.excluded_date.date() for ex in excluded_dates}
        modified_dict = {mod.original_date.date(): mod for mod in modified_instances}

        # Iterate through all occurrences generated by the ruleset
        for instance_dt_local in ruleset:
            # instance_dt_local from rrule is aware in the original local timezone

            # Stop iterating if we go too far past the requested range? Might be needed for infinite rules.
            # For now, rely on COUNT/UNTIL inherent in ruleset and the caller's filter.
            # Consider adding a safety break for truly infinite rules if performance becomes an issue.
            # Example safety break (could be refined):
            # if instance_dt_local > range_end_local + timedelta(days=365): # Stop if instance starts >1yr after range end
            #     logger.warning(f"Stopping infinite recurrence expansion early for event {base_event.id}")
            #     break

            # Convert instance start time to UTC for consistent internal representation and lookups
            instance_dt_utc = self.normalize_to_utc(instance_dt_local)
            instance_date_naive = instance_dt_utc.date()  # Use UTC date for lookup keys

            # 1. Check for exclusions (using naive UTC date)
            if instance_date_naive in excluded_set:
                continue

            occurrence_data = {}

            # 2. Check for modifications
            if instance_date_naive in modified_dict:
                mod_instance = modified_dict[instance_date_naive]
                # Use modified data
                occurrence_data["base_event_id"] = base_event.id
                # Normalize modified times to UTC. Assume modified times are naive
                # in the timezone specified in the modification or the base event.
                mod_tz = (
                    mod_instance.time_zone or base_event.time_zone
                )  # TZ for the modification itself
                try:
                    # Modification times are naive, assume they are in mod_tz
                    mod_start_aware_utc = self.normalize_to_utc(
                        mod_instance.start_time, tz_name=mod_tz
                    )
                    mod_end_aware_utc = self.normalize_to_utc(mod_instance.end_time, tz_name=mod_tz)
                except ValueError:
                    logger.warning(
                        f"Invalid timezone '{mod_tz}' for modified instance {mod_instance.id}, skipping."
                    )
                    continue  # Skip this modified instance

                occurrence_data["instance_start_time"] = mod_start_aware_utc  # Aware UTC
                occurrence_data["start_time"] = mod_start_aware_utc  # Aware UTC
                occurrence_data["end_time"] = mod_end_aware_utc  # Aware UTC
                occurrence_data["time_zone"] = (
                    mod_tz  # Store the effective timezone of the modification
                )
                occurrence_data["title"] = (
                    mod_instance.title if mod_instance.title is not None else base_event.title
                )
                occurrence_data["location"] = (
                    mod_instance.location
                    if mod_instance.location is not None
                    else base_event.location
                )
                occurrence_data["notes"] = (
                    mod_instance.notes if mod_instance.notes is not None else base_event.notes
                )
                occurrence_data["rrule"] = None  # Modified instances don't recur
                occurrence_data["modified_instance_id"] = mod_instance.id
            else:
                # 3. Generate standard occurrence from base event
                # instance_dt_utc is the correct aware UTC start time for this standard instance
                instance_end_utc = instance_dt_utc + duration  # Add original duration
                occurrence_data["base_event_id"] = base_event.id
                occurrence_data["instance_start_time"] = instance_dt_utc  # Aware UTC
                occurrence_data["start_time"] = instance_dt_utc  # Aware UTC
                occurrence_data["end_time"] = instance_end_utc  # Aware UTC
                occurrence_data["time_zone"] = base_event.time_zone  # Original timezone
                occurrence_data["title"] = base_event.title
                occurrence_data["location"] = base_event.location
                occurrence_data["notes"] = base_event.notes
                occurrence_data["rrule"] = None
                occurrence_data["modified_instance_id"] = None

            occurrences.append(occurrence_data)

        return occurrences

    # TODO: Implement methods
    # ... other helpers ...
