import base64
import logging

from .models import MediaItem
from .utils import fixed32_to_float, int32_to_float, int64_to_float, urlsafe_base64

logger = logging.getLogger(__name__)


def _get_nested(data: dict, *path: str):
    """Safely read nested dict fields using string keys."""
    current = data
    for key in path:
        if not isinstance(current, dict):
            return None
        if key not in current:
            return None
        current = current[key]
    return current


def _to_string(value) -> str | None:
    """Convert a scalar value to string."""
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value
    return str(value)


def _to_int(value, default: int = 0) -> int:
    """Convert a scalar value to int."""
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _to_optional_int(value) -> int | None:
    """Return int when value exists, otherwise None."""
    if value is None:
        return None
    return _to_int(value)


def _to_int32_float(value) -> float | None:
    """Decode protobuf int32-as-float representation safely."""
    raw = _to_optional_int(value)
    if raw is None:
        return None
    return int32_to_float(raw)


def _to_int64_float(value) -> float | None:
    """Decode protobuf int64-as-float representation safely."""
    raw = _to_optional_int(value)
    if raw is None:
        return None
    return int64_to_float(raw)


def _to_fixed32_float(value) -> float | None:
    """Decode protobuf fixed32 geo coordinate safely."""
    raw = _to_optional_int(value)
    if raw is None:
        return None
    return fixed32_to_float(raw)


def _parse_media_item(d: dict) -> MediaItem:
    """Parse a single media item from the raw data."""

    media_key = _to_string(d.get("1"))
    if not media_key:
        raise RuntimeError("Error parsing media_key")

    d2 = d.get("2", {}) if isinstance(d.get("2"), dict) else {}
    d5 = d.get("5", {}) if isinstance(d.get("5"), dict) else {}
    d17 = d.get("17", {}) if isinstance(d.get("17"), dict) else {}

    dedup_key = ""
    dedup_container = d2.get("21", {}) if isinstance(d2.get("21"), dict) else {}
    for key, value in dedup_container.items():
        if str(key).startswith("1"):
            dedup_key = _to_string(value) or ""
            break
    if not dedup_key:
        hash_bytes = _get_nested(d, "2", "13", "1")
        if isinstance(hash_bytes, bytes):
            dedup_key = urlsafe_base64(base64.b64encode(hash_bytes).decode())
    if not dedup_key:
        dedup_key = media_key

    origin_map = {
        1: "self",
        3: "partner",
        4: "shared",
    }

    props = d2.get("5", [])
    if isinstance(props, dict):
        props = [props]
    if not isinstance(props, list):
        props = []

    caption_raw = next((value for key, value in d2.items() if str(key).startswith("3")), None)
    caption = _to_string(caption_raw)

    item = MediaItem(
        media_key=media_key,
        caption=caption or None,
        file_name=_to_string(d2.get("4")) or f"{media_key}.bin",
        dedup_key=dedup_key,
        is_canonical=not any(isinstance(prop, dict) and _to_int(prop.get("1")) == 27 for prop in props),
        type=_to_int(_get_nested(d5, "1")),
        collection_id=_to_string(_get_nested(d2, "1", "1")) or "",
        size_bytes=_to_int(d2.get("10")),
        timezone_offset=_to_int(d2.get("8")),
        utc_timestamp=_to_int(d2.get("7")),
        server_creation_timestamp=_to_int(d2.get("9")),
        upload_status=_to_optional_int(d2.get("11")),
        quota_charged_bytes=_to_int(_get_nested(d2, "35", "2")),
        origin=origin_map.get(_to_int(_get_nested(d2, "30", "1"), 1), "self"),
        content_version=_to_int(d2.get("26")),
        trash_timestamp=_to_int(_get_nested(d2, "16", "3")),
        is_archived=_to_int(_get_nested(d2, "29", "1")) == 1,
        is_favorite=_to_int(_get_nested(d2, "31", "1")) == 1,
        is_locked=_to_int(_get_nested(d2, "39", "1")) == 1,
        is_original_quality=_to_int(_get_nested(d2, "35", "3")) == 2,
    )

    item.latitude = _to_fixed32_float(_get_nested(d17, "1", "1"))
    item.longitude = _to_fixed32_float(_get_nested(d17, "1", "2"))
    item.location_name = _to_string(_get_nested(d17, "5", "2", "1"))
    item.location_id = _to_string(_get_nested(d17, "5", "3"))

    photo_data = d5.get("2", {}) if isinstance(d5.get("2"), dict) else {}
    if photo_data:
        # photo
        item.is_edited = "4" in photo_data
        item.remote_url = _to_string(_get_nested(photo_data, "1", "1")) or item.remote_url
        item.width = _to_optional_int(_get_nested(photo_data, "1", "9", "1"))
        item.height = _to_optional_int(_get_nested(photo_data, "1", "9", "2"))
        if _get_nested(photo_data, "1", "9", "5"):
            item.make = _to_string(_get_nested(photo_data, "1", "9", "5", "1"))
            item.model = _to_string(_get_nested(photo_data, "1", "9", "5", "2"))
            item.aperture = _to_int32_float(_get_nested(photo_data, "1", "9", "5", "4"))
            item.shutter_speed = _to_int32_float(_get_nested(photo_data, "1", "9", "5", "5"))
            item.iso = _to_optional_int(_get_nested(photo_data, "1", "9", "5", "6"))
            item.focal_length = _to_int32_float(_get_nested(photo_data, "1", "9", "5", "7"))

    video_data = d5.get("3", {}) if isinstance(d5.get("3"), dict) else {}
    if video_data:
        # video
        item.remote_url = _to_string(_get_nested(video_data, "2", "1")) or item.remote_url
        if isinstance(video_data.get("4"), dict):
            item.duration = _to_optional_int(_get_nested(video_data, "4", "1"))
            item.width = _to_optional_int(_get_nested(video_data, "4", "4"))
            item.height = _to_optional_int(_get_nested(video_data, "4", "5"))
        item.capture_frame_rate = _to_int64_float(_get_nested(video_data, "6", "4"))
        item.encoded_frame_rate = _to_int64_float(_get_nested(video_data, "6", "5"))

    micro_video_data = _get_nested(d5, "5", "2", "4")
    if isinstance(micro_video_data, dict):
        # micro video
        item.is_micro_video = True
        item.duration = _to_optional_int(micro_video_data.get("1"))
        item.micro_video_width = _to_optional_int(micro_video_data.get("4"))
        item.micro_video_height = _to_optional_int(micro_video_data.get("5"))

    return item


def _parse_deletion_item(d: dict) -> str | None:
    """Parse a single deletion item from the raw data."""
    type = _to_int(_get_nested(d, "1", "1"))
    if type == 1:
        return _to_string(_get_nested(d, "1", "2", "1"))
    return None
    # if type == 4:
    #     return d["1"]["5"]["2"]
    # if type == 6:
    #     return d["1"]["7"]["1"]


# def _parse_collection_item(d: dict) -> CollectionItem:
#     """Parse a single collection item from the raw data."""
#     return CollectionItem(
#         collection_media_key=d["1"],
#         collection_album_id=d["4"]["2"]["3"],
#         cover_item_media_key=d["2"].get("17", {}).get("1"),
#         start=d["2"]["10"]["6"]["1"],
#         end=d["2"]["10"]["7"]["1"],
#         last_activity_time_ms=d["2"]["10"]["10"],
#         title=d["2"]["5"],
#         total_items=d["2"]["7"],
#         type=d["2"]["8"],
#         sort_order=d["19"]["1"],
#         is_custom_ordered=d["19"]["2"] == 1,
#     )


# def _parse_envelope_item(d: dict) -> EnvelopeItem:
#     """Parse a single envelope item from the raw data."""
#     return EnvelopeItem(media_key=d["1"]["1"], hint_time_ms=d["2"])


def _get_items_list(data: dict, key: str) -> list[dict]:
    """Helper to get a list of items from the data, handling single item case."""
    root = data.get("1", {}) if isinstance(data, dict) else {}
    items = root.get(key, []) if isinstance(root, dict) else []
    return [items] if isinstance(items, dict) else items


def parse_db_update(data: dict) -> tuple[str, str | None, list[MediaItem], list[str]]:
    """
    Parse the library state from the raw data.

    Returns:
        tuple containing:
        - sync_token: Token for next sync cycle (NEXT_SYNC in Google Photos app)
        - resume_token: Token for pagination within current sync (INITIAL_RESUME/DELTA_RESUME in app)
        - remote_media: List of parsed media items
        - media_keys_to_delete: List of media keys to delete
    """
    root = data.get("1", {}) if isinstance(data, dict) else {}
    resume_token = _to_string(root.get("1")) or ""
    sync_token = _to_string(root.get("6")) or ""

    # Parse media items
    remote_media = []
    media_items = _get_items_list(data, "2")
    for d in media_items:
        if not isinstance(d, dict):
            logger.warning("Skipping non-dict media item entry: %s", type(d).__name__)
            continue
        try:
            remote_media.append(_parse_media_item(d))
        except Exception:
            logger.warning("Failed to parse media item (media_key=%s)", d.get("1", "unknown"), exc_info=True)

    deletions = _get_items_list(data, "9")
    media_keys_to_delete = [media_key for d in deletions if (media_key := _parse_deletion_item(d))]

    # collections = _get_items_list(data, "3")
    # remote_media.extend(_parse_collection_item(d) for d in collections)

    # envelopes = _get_items_list(data, "12")
    # for d in envelopes:
    #     _parse_envelope_item(d)

    return sync_token, resume_token, remote_media, media_keys_to_delete
