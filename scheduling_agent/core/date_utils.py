"""Date and time utility functions."""

from datetime import datetime, timezone, date, time, timedelta
from typing import Optional, List, Generator, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from dateutil import rrule
from dateutil.parser import parse as dateutil_parse
from functools import lru_cache

from scheduling_agent.db.models import Event, ExcludedDate, ModifiedInstance
from scheduling_agent.logging_config import get_logger

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
                return dt.replace(tzinfo=timezone.utc)
            
            # If it has timezone info, normalize it to standard library UTC
            return dt.astimezone(timezone.utc)
        except Exception as e:
            logger.error(f"Failed to parse datetime string: '{dt_str}'", error=str(e))
            raise ValueError(f"Invalid datetime format: {dt_str}") from e

    def normalize_to_utc(self, dt: datetime, tz_name: Optional[str] = None) -> datetime:
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
                logger.warning(f"Invalid timezone '{local_tz_name}' provided for naive datetime, assuming UTC.")
                dt = dt.replace(tzinfo=timezone.utc)
            except Exception as e:
                logger.error("Error applying timezone to naive datetime", exc_info=True)
                # Fallback to assuming UTC
                dt = dt.replace(tzinfo=timezone.utc)

        # Now dt is guaranteed to be aware, convert to UTC
        return dt.astimezone(timezone.utc)

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
            dt_utc = dt_utc.replace(tzinfo=timezone.utc)
        
        try:
            target_tz = ZoneInfo(tz_name)
            return dt_utc.astimezone(target_tz)
        except ZoneInfoNotFoundError:
            logger.error(f"Invalid target timezone name: '{tz_name}'")
            raise ValueError(f"Invalid target timezone: {tz_name}")
        except Exception as e:
            logger.error("Error converting timezone", exc_info=True)
            raise ValueError(f"Could not convert to timezone: {tz_name}")

    @lru_cache(maxsize=128)
    def _parse_rrule_string(self, rrule_str: str, dtstart: datetime) -> Optional[rrule.rruleset]:
        """Parses an RRULE string into a dateutil rruleset, with caching."""
        if not rrule_str:
            return None
        try:
            # dateutil.rrule.rrulestr requires aware dtstart
            if dtstart.tzinfo is None:
                dtstart = dtstart.replace(tzinfo=timezone.utc)

            # TODO: Handle potential EXDATE/RDATE within the rrule_str itself if needed
            # Currently assuming rrule_str is just the main RRULE part.
            rule = rrule.rrulestr(rrule_str, dtstart=dtstart, forceset=True, cache=True)
            return rule
        except Exception as e:
            logger.error(f"Failed to parse RRULE string: '{rrule_str}'", error=str(e))
            return None # Return None or raise an error?

    def expand_recurrence(
        self,
        base_event: Event,
        range_start_utc: datetime,
        range_end_utc: datetime,
        excluded_dates: List[ExcludedDate],
        modified_instances: List[ModifiedInstance],
    ) -> List[dict]: # Returning list of dicts for now
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
            return occurrences # Not a recurring event

        # Base event times are stored as NAIVE UTC.
        base_start_naive_utc = base_event.start_time
        base_end_naive_utc = base_event.end_time
        original_tz_name = base_event.time_zone

        # Calculate duration based on naive UTC times (consistent)
        duration = base_end_naive_utc - base_start_naive_utc

        # Convert the naive UTC start time to the ORIGINAL LOCAL timezone aware time for rrule
        try:
            original_local_dtstart = self.convert_from_utc(base_start_naive_utc.replace(tzinfo=timezone.utc), original_tz_name)
        except ValueError:
            logger.error(f"Invalid original timezone '{original_tz_name}' for event {base_event.id}, cannot expand recurrence accurately.")
            return occurrences # Cannot proceed without valid original TZ

        # Parse the RRULE string using the AWARE LOCAL start time
        ruleset = self._parse_rrule_string(base_event.rrule, dtstart=original_local_dtstart)
        if not ruleset:
            return occurrences # Could not parse rule

        # Prepare lookup sets/dicts for exclusions and modifications
        excluded_set = {ex.excluded_date.date() for ex in excluded_dates}
        modified_dict = {mod.original_date.date(): mod for mod in modified_instances}

        # Generate occurrences. rrule.between works with aware datetime objects.
        # We need to convert the UTC range to the event's local timezone for accurate 'between' comparison
        # if the rrule is based on local times.
        try:
            range_start_local = self.convert_from_utc(range_start_utc, original_tz_name)
            range_end_local = self.convert_from_utc(range_end_utc, original_tz_name)
        except ValueError:
             logger.error(f"Invalid original timezone '{original_tz_name}' for event {base_event.id} during range conversion.")
             return occurrences

        # Generate instances in the LOCAL timezone
        instance_datetimes_local = ruleset.between(
            range_start_local,   # Use range converted to local timezone
            range_end_local,     # Use range converted to local timezone
            inc=True # Include boundaries
        )

        for instance_dt_local in instance_datetimes_local:
            # instance_dt_local from rrule is aware in the original local timezone
            # Convert instance start time to UTC for consistent internal representation and lookups
            instance_dt_utc = self.normalize_to_utc(instance_dt_local)
            instance_date_naive = instance_dt_utc.date() # Use UTC date for lookup keys

            # 1. Check for exclusions (using naive UTC date)
            if instance_date_naive in excluded_set:
                continue

            occurrence_data = {}

            # 2. Check for modifications
            if instance_date_naive in modified_dict:
                mod_instance = modified_dict[instance_date_naive]
                # Use modified data
                occurrence_data['base_event_id'] = base_event.id
                # Normalize modified times to UTC. Assume modified times are naive
                # in the timezone specified in the modification or the base event.
                mod_tz = mod_instance.time_zone or base_event.time_zone # TZ for the modification itself
                try:
                    # Modification times are naive, assume they are in mod_tz
                    mod_start_aware_utc = self.normalize_to_utc(mod_instance.start_time, tz_name=mod_tz)
                    mod_end_aware_utc = self.normalize_to_utc(mod_instance.end_time, tz_name=mod_tz)
                except ValueError:
                    logger.warning(f"Invalid timezone '{mod_tz}' for modified instance {mod_instance.id}, skipping.")
                    continue # Skip this modified instance

                occurrence_data['instance_start_time'] = mod_start_aware_utc # Aware UTC
                occurrence_data['start_time'] = mod_start_aware_utc            # Aware UTC
                occurrence_data['end_time'] = mod_end_aware_utc              # Aware UTC
                occurrence_data['time_zone'] = mod_tz # Store the effective timezone of the modification
                occurrence_data['title'] = mod_instance.title if mod_instance.title is not None else base_event.title
                occurrence_data['location'] = mod_instance.location if mod_instance.location is not None else base_event.location
                occurrence_data['notes'] = mod_instance.notes if mod_instance.notes is not None else base_event.notes
                occurrence_data['rrule'] = None # Modified instances don't recur
                occurrence_data['modified_instance_id'] = mod_instance.id
            else:
                # 3. Generate standard occurrence from base event
                # instance_dt_utc is the correct aware UTC start time for this standard instance
                instance_end_utc = instance_dt_utc + duration # Add original duration
                occurrence_data['base_event_id'] = base_event.id
                occurrence_data['instance_start_time'] = instance_dt_utc # Aware UTC
                occurrence_data['start_time'] = instance_dt_utc # Aware UTC
                occurrence_data['end_time'] = instance_end_utc # Aware UTC
                occurrence_data['time_zone'] = base_event.time_zone # Original timezone
                occurrence_data['title'] = base_event.title
                occurrence_data['location'] = base_event.location
                occurrence_data['notes'] = base_event.notes
                occurrence_data['rrule'] = None
                occurrence_data['modified_instance_id'] = None

            occurrences.append(occurrence_data)

        return occurrences

    # TODO: Implement methods
    # ... other helpers ...
    pass 