# sa/event/date_utils.py
"""Date and time utility functions."""

import dateparser
from datetime import UTC, datetime, time, timedelta, date
from dateutil import rrule
from dateutil.parser import parse as dateutil_parse
from functools import lru_cache
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sa.core.logging_config import get_logger
from sa.db.models import ExcludedDate, ModifiedInstance

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
        rrule_str: str,
        base_event_id: str | int,
        base_start_naive_utc: datetime,
        base_end_naive_utc: datetime,
        base_title: str,
        base_location: str | None,
        base_notes: str | None,
        base_timezone: str,
        range_start_utc: datetime,
        range_end_utc: datetime,
        excluded_dates: list[ExcludedDate],
        modified_instances: list[ModifiedInstance],
    ) -> list[dict]:
        """Expands a recurring event within a date range, applying exclusions/modifications.

        Args:
            rrule_str: The RRULE string for the recurring event.
            base_event_id: ID of the base event.
            base_start_naive_utc: The original start time of the event, naive but representing UTC.
            base_end_naive_utc: The original end time of the event, naive but representing UTC.
            base_title: Base event title.
            base_location: Base event location.
            base_notes: Base event notes.
            base_timezone: IANA timezone name of the base event (where it was created/intended).
            range_start_utc: The start of the query range (aware UTC datetime).
            range_end_utc: The end of the query range (aware UTC datetime).
            excluded_dates: List of ExcludedDate objects.
            modified_instances: List of ModifiedInstance objects.

        Returns:
            A list of dictionaries, each representing an event occurrence within the range.
            Keys: 'base_event_id', 'instance_start_time' (aware UTC), 'start_time' (aware UTC),
                  'end_time' (aware UTC), 'time_zone' (str), 'title' (str), 'location' (str | None),
                  'notes' (str | None), 'rrule' (None), 'modified_instance_id' (any | None).
        """
        occurrences = []

        if not rrule_str:
            # If it's not recurring, check if the single instance falls within the range
            # Make base times UTC aware for comparison
            base_start_utc = base_start_naive_utc.replace(tzinfo=UTC)
            base_end_utc = base_end_naive_utc.replace(tzinfo=UTC)
            instance_date_naive = base_start_utc.date()

            # Check if excluded
            if instance_date_naive in {ex_date.excluded_date.date() for ex_date in excluded_dates}:
                return []

            # Check if modified
            mod_instance = next((mod for mod in modified_instances if mod.original_date.date() == instance_date_naive), None)

            occurrence_data = {}
            if mod_instance:
                # Use attribute access for Pydantic models
                mod_tz = mod_instance.time_zone or base_timezone
                try:
                    # Modification times are naive, assume they are in mod_tz
                    mod_start_naive = mod_instance.start_time
                    mod_end_naive = mod_instance.end_time
                    if not mod_start_naive or not mod_end_naive:
                        logger.warning(f"Modified instance {mod_instance.id} for event {base_event_id} is missing start/end time, skipping.")
                        return []
                    mod_start_aware_utc = self.normalize_to_utc(mod_start_naive, tz_name=mod_tz)
                    mod_end_aware_utc = self.normalize_to_utc(mod_end_naive, tz_name=mod_tz)
                except ValueError:
                    logger.warning(
                        f"Invalid timezone '{mod_tz}' for modified instance {mod_instance.id} of event {base_event_id}, skipping."
                    )
                    return [] # Skip this modified instance

                # Check if the *modified* time overlaps the range
                if mod_end_aware_utc <= range_start_utc or mod_start_aware_utc >= range_end_utc:
                    return [] # Modified instance is outside the range

                occurrence_data["base_event_id"] = base_event_id
                occurrence_data["instance_start_time"] = mod_start_aware_utc # Use modified start as the instance identifier
                occurrence_data["start_time"] = mod_start_aware_utc # Aware UTC
                occurrence_data["end_time"] = mod_end_aware_utc # Aware UTC
                occurrence_data["time_zone"] = mod_tz
                occurrence_data["title"] = mod_instance.title or base_title # Handle None title
                occurrence_data["location"] = mod_instance.location # Assumes location can be None
                occurrence_data["notes"] = mod_instance.notes # Assumes notes can be None
                occurrence_data["rrule"] = None
                occurrence_data["modified_instance_id"] = mod_instance.id

            else:
                 # Standard non-recurring instance, check overlap
                if base_end_utc <= range_start_utc or base_start_utc >= range_end_utc:
                    return [] # Instance is outside the range

                occurrence_data["base_event_id"] = base_event_id
                occurrence_data["instance_start_time"] = base_start_utc # Aware UTC
                occurrence_data["start_time"] = base_start_utc # Aware UTC
                occurrence_data["end_time"] = base_end_utc # Aware UTC
                occurrence_data["time_zone"] = base_timezone
                occurrence_data["title"] = base_title
                occurrence_data["location"] = base_location
                occurrence_data["notes"] = base_notes
                occurrence_data["rrule"] = None
                occurrence_data["modified_instance_id"] = None

            return [occurrence_data]


        # --- Start of recurring event logic ---

        # Calculate duration based on naive UTC times (consistent)
        duration = base_end_naive_utc - base_start_naive_utc

        # Convert the naive UTC start time to the ORIGINAL LOCAL timezone aware time for rrule
        try:
            # Make base_start_naive_utc aware UTC before converting
            original_local_dtstart = self.convert_from_utc(
                base_start_naive_utc.replace(tzinfo=UTC), base_timezone
            )
        except ValueError:
            logger.exception(
                f"Invalid original timezone '{base_timezone}' for event {base_event_id}, cannot expand recurrence accurately."
            )
            return occurrences  # Cannot proceed without valid original TZ

        # Parse the RRULE string using the AWARE LOCAL start time
        ruleset = DateUtils._parse_rrule_string(rrule_str, dtstart=original_local_dtstart)
        if not ruleset:
            logger.warning(f"Could not parse rrule string '{rrule_str}' for event {base_event_id}")
            return occurrences  # Could not parse rule

        # Prepare lookup sets/dicts for exclusions and modifications
        # Extract the date part (naive UTC) for lookups
        excluded_set = {ex_date.excluded_date.date() for ex_date in excluded_dates}
        # Add logging for the exclusion set
        logger.debug(f"Exclusion set (dates): {excluded_set}")
        # Use attribute access and ensure we use the date part as the key
        modified_dict = {mod.original_date.date(): mod for mod in modified_instances}

        # *** Add safeguard for unbounded rules ***
        if "COUNT=" not in rrule_str.upper() and "UNTIL=" not in rrule_str.upper():
            logger.warning(
                f"Recurrence rule for event {base_event_id} has no COUNT or UNTIL. "
                f"Expansion requires a bounded rule. Returning empty list.",
                rule=rrule_str
            )
            return []

        # --- Iterate through generated instances manually --- #
        for instance_dt_local in ruleset:
            instance_dt_utc = self.normalize_to_utc(instance_dt_local)
            instance_date_naive = instance_dt_utc.date()  # Use naive UTC date for lookup keys
            # Add logging for the instance being checked
            logger.debug(f"Checking instance: UTC={instance_dt_utc.isoformat()}, NaiveDate={instance_date_naive.isoformat()}")
            modified = False
            mod_instance = None

            logger.debug(
                f"RRULE generated instance (local): {instance_dt_local.isoformat()} ({instance_dt_local.tzinfo}), "
                f"UTC: {instance_dt_utc.isoformat()}"
            )

            # 1. Check if this ORIGINAL date is modified
            mod_instance = modified_dict.get(instance_date_naive)
            effective_start_utc = instance_dt_utc
            effective_end_utc = instance_dt_utc + duration
            effective_tz = base_timezone
            effective_title = base_title
            effective_location = base_location
            effective_notes = base_notes
            mod_id = None

            if mod_instance:
                modified = True
                mod_tz = mod_instance.time_zone or base_timezone
                mod_start_naive = mod_instance.start_time
                mod_end_naive = mod_instance.end_time

                if not mod_start_naive or not mod_end_naive:
                    logger.warning(f"Modified instance {mod_instance.id} on {instance_date_naive} "
                                   f"for event {base_event_id} is missing start/end time. Using calculated instance time.")
                    # Keep effective times as calculated original instance time
                    effective_tz = base_timezone # Revert TZ
                    # Keep modified flag potentially true but use original timing?
                    # Let's revert modified flag too, to avoid using modified details with original time
                    modified = False
                else:
                    try:
                        effective_start_utc = self.normalize_to_utc(mod_start_naive, tz_name=mod_tz)
                        effective_end_utc = self.normalize_to_utc(mod_end_naive, tz_name=mod_tz)
                        effective_tz = mod_tz
                        effective_title = mod_instance.title or base_title
                        effective_location = mod_instance.location
                        effective_notes = mod_instance.notes
                        mod_id = mod_instance.id
                    except ValueError:
                        logger.warning(
                            f"Invalid timezone '{mod_tz}' for modified instance {mod_instance.id} "
                            f"on {instance_date_naive} for event {base_event_id}, skipping modification application."
                        )
                        modified = False # Treat as not modified, fallback to original time/details
                        # effective times remain original calculated times
                        effective_tz = base_timezone
                        effective_title = base_title
                        effective_location = base_location
                        effective_notes = base_notes
                        mod_id = None

            # 2. *** Perform Range Check using EFFECTIVE times ***
            if effective_start_utc >= range_end_utc:
                logger.debug(f"Effective start {effective_start_utc} is after range end {range_end_utc}, stopping iteration.")
                break # Assumes instances are generated chronologically based on original date

            if effective_end_utc <= range_start_utc:
                logger.debug(f"Effective end {effective_end_utc} is before range start {range_start_utc}, skipping.")
                continue

            # 3. Check for exclusions (using naive UTC date of the ORIGINAL instance)
            if instance_date_naive in excluded_set:
                logger.debug(f"Original instance date {instance_date_naive} is excluded, skipping.")
                continue

            # 4. Construct occurrence data using effective details
            occurrence_data = {
                "base_event_id": base_event_id,
                "instance_start_time": effective_start_utc, # This identifies the occurrence time
                "start_time": effective_start_utc, # Aware UTC
                "end_time": effective_end_utc, # Aware UTC
                "time_zone": effective_tz,
                "title": effective_title,
                "location": effective_location,
                "notes": effective_notes,
                "rrule": None,
                "modified_instance_id": mod_id
            }

            occurrences.append(occurrence_data)

        # Sort occurrences by start time
        occurrences.sort(key=lambda x: x['start_time'])

        return occurrences

    # Add a simpler expansion method for pattern checks
    def expand_recurrence_simple(
        self, dtstart: datetime, rrule_str: str, until: datetime | None = None
    ) -> list[datetime]:
        """Expands a simple RRULE string from a start datetime up to an optional end.

        This version does NOT handle exclusions or modifications.
        It's primarily intended for pattern conflict checking.

        Args:
            dtstart: The aware UTC start datetime of the recurrence.
            rrule_str: The RRULE string.
            until: An optional aware UTC datetime to limit the expansion.

        Returns:
            A list of aware UTC datetimes representing the occurrences.
        """
        ruleset = self._parse_rrule_string(rrule_str, dtstart)
        if not ruleset:
            return []

        # dateutil requires aware datetimes for comparison
        if until and until.tzinfo is None:
            until = until.replace(tzinfo=UTC)
        if dtstart.tzinfo is None:
            dtstart = dtstart.replace(tzinfo=UTC)

        # Use .between or simply generate occurrences up to 'until'
        # Using a simple approach here: generate and filter
        # Consider dateutil's .between() for potentially better performance with large ranges
        occurrences = []
        # Choose a reasonable upper limit if 'until' is not provided
        # to prevent infinite loops for rules without COUNT/UNTIL.
        # Defaulting to 1 year for safety in pattern checks.
        effective_until = until or (dtstart + timedelta(days=366))

        try:
            for occ in ruleset:
                if occ >= effective_until:
                    break
                # Ensure occurrence is timezone-aware (should be from dateutil)
                if occ.tzinfo is None:
                    occ = occ.replace(tzinfo=UTC) # Should already be UTC based on dtstart
                
                # Only include occurrences starting *after or at* the initial dtstart
                # and *before* the effective_until limit.
                # Note: dateutil rrule includes dtstart itself if it matches the rule.
                if occ >= dtstart and occ < effective_until:
                     occurrences.append(occ)
        except Exception as e:
            logger.exception(f"Error during simple recurrence expansion for {rrule_str}", error=str(e))
            return [] # Return empty list on error

        return occurrences
