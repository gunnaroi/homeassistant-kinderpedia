"""Sensor platform for Kinderpedia."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SCHOOL_WEEKDAYS
from .coordinator import KinderpediaConfigEntry, KinderpediaDataUpdateCoordinator
from .entity import KinderpediaChildEntity

# (unique-id slug, day-entry field)
_WEEK_SENSORS: tuple[tuple[str, str], ...] = (
    ("breakfast_week", "breakfast_percent"),
    ("lunch_week", "lunch_percent"),
    ("nap_week", "nap_duration"),
)

# Sensors that show today's or latest data
_TODAY_SENSORS = ("checkout", "checkin_staff", "attendance")


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: KinderpediaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Kinderpedia sensors, including children discovered later."""
    coordinator = config_entry.runtime_data.coordinator
    tracked_keys: set[str] = set()

    @callback
    def _discover_new_children() -> None:
        data = coordinator.data or {}
        new_sensors: list[SensorEntity] = []

        for key, child_data in data.get("children", {}).items():
            if key in tracked_keys:
                continue
            tracked_keys.add(key)

            child = child_data["child"]
            args = (
                coordinator,
                child["child_id"],
                child["kindergarten_id"],
                f"{child['first_name']} {child['last_name']}".strip(),
                child["first_name"],
            )
            new_sensors.append(KinderpediaChildInfoSensor(*args))
            new_sensors.extend(
                KinderpediaWeekSensor(*args, sensor_type=slug, field=field)
                for slug, field in _WEEK_SENSORS
            )
            new_sensors.append(KinderpediaNewsfeedSensor(*args))
            new_sensors.append(KinderpediaCheckoutSensor(*args))
            new_sensors.append(KinderpediaCheckinstaffSensor(*args))
            new_sensors.append(KinderpediaAttendanceSensor(*args))

        if new_sensors:
            async_add_entities(new_sensors)

    _discover_new_children()
    config_entry.async_on_unload(coordinator.async_add_listener(_discover_new_children))


class KinderpediaChildInfoSensor(KinderpediaChildEntity, SensorEntity):
    """Static information about the child."""

    def __init__(
        self,
        coordinator: KinderpediaDataUpdateCoordinator,
        child_id: int,
        kg_id: int,
        device_name: str,
        first_name: str,
    ) -> None:
        super().__init__(coordinator, child_id, kg_id, device_name)
        self._attr_unique_id = f"{DOMAIN}_child_info_{child_id}_{kg_id}"
        self._attr_name = first_name.lower()

    @property
    def native_value(self) -> str | None:
        child = self._child_data.get("child")
        if not child:
            return None
        return f"{child.get('first_name', '')} {child.get('last_name', '')}".strip()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        child = self._child_data.get("child")
        if not child:
            return {}
        return {
            "birth_date": child.get("birth_date"),
            "gender": "female" if child.get("gender") == "f" else "male",
            "kindergarten": child.get("kindergarten_name"),
            "last_updated": self._last_updated,
        }


class KinderpediaWeekSensor(KinderpediaChildEntity, SensorEntity):
    """Weekly aggregate of a single day metric, Mon-Fri as attributes."""

    def __init__(
        self,
        coordinator: KinderpediaDataUpdateCoordinator,
        child_id: int,
        kg_id: int,
        device_name: str,
        first_name: str,
        *,
        sensor_type: str,
        field: str,
    ) -> None:
        super().__init__(coordinator, child_id, kg_id, device_name)
        self._field = field
        self._attr_unique_id = f"{DOMAIN}_{sensor_type}_{child_id}_{kg_id}"
        self._attr_name = f"{first_name.lower()} {sensor_type.replace('_', ' ')}"

    @property
    def native_value(self) -> str:
        return (self._last_updated or "")[:10]  # date portion

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        week = self._week_days()
        attrs: dict[str, Any] = {"last_updated": self._last_updated}
        for weekday in SCHOOL_WEEKDAYS:
            attrs[weekday] = week.get(weekday, {}).get(self._field, 0)
        return attrs


class KinderpediaNewsfeedSensor(KinderpediaChildEntity, SensorEntity):
    """Latest newsfeed activity for a child."""

    _attr_icon = "mdi:newspaper-variant-outline"

    def __init__(
        self,
        coordinator: KinderpediaDataUpdateCoordinator,
        child_id: int,
        kg_id: int,
        device_name: str,
        first_name: str,
    ) -> None:
        super().__init__(coordinator, child_id, kg_id, device_name)
        self._attr_unique_id = f"{DOMAIN}_newsfeed_{child_id}_{kg_id}"
        self._attr_name = f"{first_name.lower()} newsfeed"

    def _feed(self) -> list[dict]:
        return self._child_data.get("newsfeed", [])

    @property
    def native_value(self) -> str | None:
        feed = self._feed()
        return feed[0].get("summary", "")[:255] if feed else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {"last_updated": self._last_updated}
        feed = self._feed()
        if not feed:
            return attrs

        attrs["latest_date"] = feed[0].get("date")
        separator = "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        attrs["recent"] = separator.join(
            f"📅 {item.get('date', '')}\n{item.get('summary', '')}" for item in feed[:10]
        )
        return attrs


class KinderpediaCheckoutSensor(KinderpediaChildEntity, SensorEntity):
    """Child's checkout/departure time for today or latest school day."""

    def __init__(
        self,
        coordinator: KinderpediaDataUpdateCoordinator,
        child_id: int,
        kg_id: int,
        device_name: str,
        first_name: str,
    ) -> None:
        super().__init__(coordinator, child_id, kg_id, device_name)
        self._attr_unique_id = f"{DOMAIN}_checkout_{child_id}_{kg_id}"
        self._attr_name = f"{first_name.lower()} checkout time"

    @property
    def native_value(self) -> str | None:
        day_info = self._latest_school_day()
        if not day_info:
            return None
        return day_info.get("checkout")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        day_info = self._latest_school_day()
        attrs: dict[str, Any] = {"last_updated": self._last_updated}
        if day_info:
            attrs["date"] = day_info.get("date")
            attrs["checkin"] = day_info.get("checkin")
        return attrs

    def _latest_school_day(self) -> dict | None:
        """Return the most recent day with school activity."""
        days = self._days
        for date_iso in sorted(days.keys(), reverse=True):
            day = days[date_iso]
            if day.get("checkin") and day.get("checkin") != "unknown":
                return day
        return None


class KinderpediaCheckinstaffSensor(KinderpediaChildEntity, SensorEntity):
    """The caregiver/teacher who did check-in today or on latest school day."""

    def __init__(
        self,
        coordinator: KinderpediaDataUpdateCoordinator,
        child_id: int,
        kg_id: int,
        device_name: str,
        first_name: str,
    ) -> None:
        super().__init__(coordinator, child_id, kg_id, device_name)
        self._attr_unique_id = f"{DOMAIN}_checkin_staff_{child_id}_{kg_id}"
        self._attr_name = f"{first_name.lower()} checkin staff"

    @property
    def native_value(self) -> str | None:
        day_info = self._latest_school_day()
        if not day_info:
            return None
        return day_info.get("checkin_staff")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        day_info = self._latest_school_day()
        attrs: dict[str, Any] = {"last_updated": self._last_updated}
        if day_info:
            attrs["date"] = day_info.get("date")
            attrs["checkin"] = day_info.get("checkin")
        return attrs

    def _latest_school_day(self) -> dict | None:
        """Return the most recent day with school activity."""
        days = self._days
        for date_iso in sorted(days.keys(), reverse=True):
            day = days[date_iso]
            if day.get("checkin") and day.get("checkin") != "unknown":
                return day
        return None


class KinderpediaAttendanceSensor(KinderpediaChildEntity, SensorEntity):
    """Attendance status for today or latest school day."""

    def __init__(
        self,
        coordinator: KinderpediaDataUpdateCoordinator,
        child_id: int,
        kg_id: int,
        device_name: str,
        first_name: str,
    ) -> None:
        super().__init__(coordinator, child_id, kg_id, device_name)
        self._attr_unique_id = f"{DOMAIN}_attendance_{child_id}_{kg_id}"
        self._attr_name = f"{first_name.lower()} attendance"

    @property
    def native_value(self) -> str | None:
        day_info = self._latest_day()
        if not day_info:
            return None
        return self._get_attendance_status(day_info)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        day_info = self._latest_day()
        attrs: dict[str, Any] = {"last_updated": self._last_updated}
        if day_info:
            attrs["date"] = day_info.get("date")
            if day_info.get("absent"):
                attrs["reason"] = day_info.get("absence_reason", "")
                attrs["motivated"] = day_info.get("absence_motivated", False)
        return attrs

    def _latest_day(self) -> dict | None:
        """Return the most recent day with any data."""
        days = self._days
        for date_iso in sorted(days.keys(), reverse=True):
            return days[date_iso]
        return None

    @staticmethod
    def _get_attendance_status(day_info: dict) -> str:
        """Determine attendance status from day info."""
        if day_info.get("absent"):
            return "absent"

        checkin = day_info.get("checkin", "unknown")
        if checkin == "unknown" or not checkin:
            return "unknown"

        # Check for late arrival or early departure
        if " - " in checkin:
            parts = checkin.split(" - ")
            if len(parts) >= 2:
                try:
                    checkin_hour = int(parts[0].split(":")[0])
                    if checkin_hour >= 9:
                        return "late_arrival"
                except (ValueError, IndexError):
                    pass

        return "present"
