"""
Jian Project WebSocket 数据源解析器。
负责把 Jian Project (api.sismotide.top) /all 聚合流下发的 7 大核心数据源报文
转换为统一的领域事件与 EventEnvelope。
支持源：
1. cea (中国地震预警网 CEA EEW)
2. cwa-eew (台湾中央气象署强震即时警报)
3. jma-eew (日本气象厅紧急地震速报)
4. weather (中国气象局气象预警)
5. nmefc-tsunami (自然资源部海啸预警中心)
6. cenc (中国地震台网地震测定)
7. usgs (美国地质调查局地震测定)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ...utils.china_regions import (
    extract_province_from_adcode,
    province_short,
    resolve_province_from_text,
    resolve_province_full,
    strip_province_prefix,
)
from ...utils.converters import ScaleConverter, safe_float_convert
from ...utils.plugin_logger import plugin_logger
from ...utils.time_converter import TimeConverter
from ..domain.event_identity import EventIdentity
from ..domain.event_models import (
    EarthquakeEvent,
    EventEnvelope,
    TsunamiEvent,
    WeatherEvent,
)
from ..domain.event_payload import SourcePayload
from ..services.geo.region_service import region_service
from ..sources.source_catalog import get_source_entry
from .base_parser import BaseParser


def _parse_timestamp_or_str(val: Any) -> datetime | None:
    """辅助解析时间字段（支持毫秒时间戳与 ISO 字符串）。"""
    if val is None or val == "":
        return None
    return TimeConverter.parse_datetime(val)


class CeaEewJianProjectParser(BaseParser):
    """中国地震预警网 (CEA) 预警解析器 - Jian Project。"""

    def __init__(self, message_logger=None, source_id: str = "cea_jianproject"):
        super().__init__(source_id, message_logger)

    def _parse_data(self, data: dict[str, Any]) -> EventEnvelope | None:
        try:
            msg_data = self._extract_data(data)
            if not msg_data or self._is_heartbeat_message(msg_data):
                return None

            event_id = str(msg_data.get("id") or "").strip()
            if not event_id:
                return None

            raw_report_num = msg_data.get("number", 1)
            try:
                report_num = int(raw_report_num)
            except (TypeError, ValueError):
                report_num = 1
            if report_num <= 0:
                report_num = 1

            occurred_at = _parse_timestamp_or_str(msg_data.get("originTime"))
            if not occurred_at:
                occurred_at = datetime.now(timezone.utc)

            latitude = safe_float_convert(msg_data.get("latitude"))
            longitude = safe_float_convert(msg_data.get("longitude"))
            depth = safe_float_convert(msg_data.get("depth"))
            magnitude = safe_float_convert(msg_data.get("magnitude"))
            place_name = str(msg_data.get("placeName") or "").strip()

            province_hint, _ = strip_province_prefix(place_name)
            province = resolve_province_full(province_hint) if province_hint else None

            source_entry = get_source_entry(self.source_id)
            metadata = {
                "source_family": "jian_project",
                "source_enum": source_entry.source_enum if source_entry else "jian_project_cea",
                "source_type": source_entry.source_type.value if source_entry else "earthquake_warning",
                "event_id": event_id,
                "province": province,
                "report_num": report_num,
                "updates": report_num,
                "is_final": False,
            }

            domain_event = EarthquakeEvent(
                occurred_at=occurred_at,
                latitude=latitude,
                longitude=longitude,
                place_name=place_name,
                magnitude=magnitude,
                depth=depth,
                province=province,
                metadata=dict(metadata),
            )

            identity = EventIdentity(
                event_id=event_id,
                source_id=self.source_id,
                event_type="earthquake_warning",
                provider_family=source_entry.provider_family.value if source_entry else "jian_project",
                source_enum=source_entry.source_enum if source_entry else "jian_project_cea",
                report_num=report_num,
                published_at=occurred_at,
                aliases=(event_id,),
                attributes={
                    "parser_name": self.source_entry.parser_name if self.source_entry else "china_eew_parser",
                    "config_key": source_entry.config_key if source_entry else "china_earthquake_warning",
                },
            )

            envelope = EventEnvelope(
                identity=identity,
                event=domain_event,
                received_at=datetime.now(timezone.utc),
                payload=SourcePayload(
                    source_id=self.source_id,
                    provider_family=source_entry.provider_family.value if source_entry else "jian_project",
                    message_type="cea",
                    raw=dict(msg_data),
                    attributes=dict(metadata),
                ),
                metadata=metadata,
            )

            plugin_logger.info(
                f"[灾害预警] CEA 地震预警解析成功: {domain_event.place_name} (M {domain_event.magnitude}) 第{report_num}报",
                is_event_linked=True,
                event_stream="earthquake",
                is_silent_window=True,
            )
            return envelope
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 解析数据失败: {exc}")
            return None


class CwaEewJianProjectParser(BaseParser):
    """台湾中央气象署强震即时警报解析器 - Jian Project。"""

    def __init__(self, message_logger=None, source_id: str = "cwa_jianproject"):
        super().__init__(source_id, message_logger)

    def _parse_data(self, data: dict[str, Any]) -> EventEnvelope | None:
        try:
            msg_data = self._extract_data(data)
            if not msg_data or self._is_heartbeat_message(msg_data):
                return None

            event_id = str(msg_data.get("id") or "").strip()
            if not event_id:
                return None

            raw_report_num = msg_data.get("number", 1)
            try:
                report_num = int(raw_report_num)
            except (TypeError, ValueError):
                report_num = 1
            if report_num <= 0:
                report_num = 1

            occurred_at = _parse_timestamp_or_str(msg_data.get("originTime"))
            if not occurred_at:
                occurred_at = datetime.now(timezone.utc)

            latitude = safe_float_convert(msg_data.get("latitude"))
            longitude = safe_float_convert(msg_data.get("longitude"))
            depth = safe_float_convert(msg_data.get("depth"))
            magnitude = safe_float_convert(msg_data.get("magnitude"))
            place_name = str(msg_data.get("placeName") or "").strip()

            source_entry = get_source_entry(self.source_id)
            metadata = {
                "source_family": "jian_project",
                "source_enum": source_entry.source_enum if source_entry else "jian_project_cwa",
                "source_type": source_entry.source_type.value if source_entry else "earthquake_warning",
                "event_id": event_id,
                "report_num": report_num,
                "updates": report_num,
                "is_final": False,
            }

            domain_event = EarthquakeEvent(
                occurred_at=occurred_at,
                latitude=latitude,
                longitude=longitude,
                place_name=place_name,
                magnitude=magnitude,
                depth=depth,
                metadata=dict(metadata),
            )

            identity = EventIdentity(
                event_id=event_id,
                source_id=self.source_id,
                event_type="earthquake_warning",
                provider_family=source_entry.provider_family.value if source_entry else "jian_project",
                source_enum=source_entry.source_enum if source_entry else "jian_project_cwa",
                report_num=report_num,
                published_at=occurred_at,
                aliases=(event_id,),
                attributes={
                    "parser_name": self.source_entry.parser_name if self.source_entry else "taiwan_eew_parser",
                    "config_key": source_entry.config_key if source_entry else "taiwan_cwa_earthquake",
                },
            )

            envelope = EventEnvelope(
                identity=identity,
                event=domain_event,
                received_at=datetime.now(timezone.utc),
                payload=SourcePayload(
                    source_id=self.source_id,
                    provider_family=source_entry.provider_family.value if source_entry else "jian_project",
                    message_type="cwa-eew",
                    raw=dict(msg_data),
                    attributes=dict(metadata),
                ),
                metadata=metadata,
            )

            plugin_logger.info(
                f"[灾害预警] CWA 强震即时警报解析成功: {domain_event.place_name} (M {domain_event.magnitude}) 第{report_num}报",
                is_event_linked=True,
                event_stream="earthquake",
                is_silent_window=True,
            )
            return envelope
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 解析数据失败: {exc}")
            return None


class JmaEewJianProjectParser(BaseParser):
    """日本气象厅紧急地震速报解析器 - Jian Project。"""

    def __init__(self, message_logger=None, source_id: str = "jma_jianproject"):
        super().__init__(source_id, message_logger)

    def _parse_data(self, data: dict[str, Any]) -> EventEnvelope | None:
        try:
            msg_data = self._extract_data(data)
            if not msg_data or self._is_heartbeat_message(msg_data):
                return None

            event_id = str(msg_data.get("id") or "").strip()
            if not event_id:
                return None

            raw_report_num = msg_data.get("serial", 1)
            try:
                report_num = int(raw_report_num)
            except (TypeError, ValueError):
                report_num = 1
            if report_num <= 0:
                report_num = 1

            occurred_at = _parse_timestamp_or_str(msg_data.get("originTime"))
            if not occurred_at:
                occurred_at = datetime.now(timezone.utc)

            latitude = safe_float_convert(msg_data.get("latitude"))
            longitude = safe_float_convert(msg_data.get("longitude"))
            depth = safe_float_convert(msg_data.get("depth"))
            magnitude = safe_float_convert(msg_data.get("magnitude"))
            place_name = str(msg_data.get("placeName") or "").strip()

            intensity_raw = str(msg_data.get("intensity") or "").strip()
            scale = ScaleConverter.parse_jma_cwa_scale(intensity_raw)
            is_final = bool(msg_data.get("isFinal", False))
            is_cancel = bool(msg_data.get("isCancel", False))
            info_type = str(msg_data.get("infoTypeName") or "").strip()
            warn_area = msg_data.get("warnArea", [])

            source_entry = get_source_entry(self.source_id)
            metadata = {
                "source_family": "jian_project",
                "source_enum": source_entry.source_enum if source_entry else "jian_project_jma",
                "source_type": source_entry.source_type.value if source_entry else "earthquake_warning",
                "event_id": event_id,
                "report_num": report_num,
                "updates": report_num,
                "is_final": is_final,
                "is_cancel": is_cancel,
                "info_type": info_type,
                "warn_area": warn_area,
            }

            domain_event = EarthquakeEvent(
                occurred_at=occurred_at,
                latitude=latitude,
                longitude=longitude,
                place_name=place_name,
                magnitude=magnitude,
                depth=depth,
                scale=scale,
                metadata=dict(metadata),
            )

            identity = EventIdentity(
                event_id=event_id,
                source_id=self.source_id,
                event_type="earthquake_warning",
                provider_family=source_entry.provider_family.value if source_entry else "jian_project",
                source_enum=source_entry.source_enum if source_entry else "jian_project_jma",
                report_num=report_num,
                published_at=occurred_at,
                is_final=is_final,
                aliases=(event_id,),
                attributes={
                    "parser_name": self.source_entry.parser_name if self.source_entry else "japan_eew_parser",
                    "config_key": source_entry.config_key if source_entry else "japan_jma_eew",
                },
            )

            envelope = EventEnvelope(
                identity=identity,
                event=domain_event,
                received_at=datetime.now(timezone.utc),
                payload=SourcePayload(
                    source_id=self.source_id,
                    provider_family=source_entry.provider_family.value if source_entry else "jian_project",
                    message_type="jma-eew",
                    raw=dict(msg_data),
                    attributes=dict(metadata),
                ),
                metadata=metadata,
            )

            plugin_logger.info(
                f"[灾害预警] JMA 紧急地震速报解析成功: {domain_event.place_name} (M {domain_event.magnitude}, 震度 {scale}) 第{report_num}报",
                is_event_linked=True,
                event_stream="earthquake",
                is_silent_window=True,
            )
            return envelope
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 解析数据失败: {exc}")
            return None


class WeatherAlarmJianProjectParser(BaseParser):
    """中国气象局气象预警解析器 - Jian Project。"""

    def __init__(self, message_logger=None, source_id: str = "china_weather_jianproject"):
        super().__init__(source_id, message_logger)
        self._processed_weather_ids: dict[str, float] = {}
        self._WEATHER_DEDUPE_WINDOW_SECONDS = 600
        self._WEATHER_DEDUPE_MAX_ENTRIES = 512

    def _is_weather_duplicate(self, weather_id: str) -> bool:
        if not weather_id:
            return False
        now = datetime.now(timezone.utc).timestamp()
        cutoff = now - self._WEATHER_DEDUPE_WINDOW_SECONDS
        self._processed_weather_ids = {
            k: t for k, t in self._processed_weather_ids.items() if t > cutoff
        }
        if weather_id in self._processed_weather_ids:
            return True
        if len(self._processed_weather_ids) >= self._WEATHER_DEDUPE_MAX_ENTRIES:
            oldest_key = min(self._processed_weather_ids, key=self._processed_weather_ids.get)
            self._processed_weather_ids.pop(oldest_key, None)
        self._processed_weather_ids[weather_id] = now
        return False

    def _parse_data(self, data: dict[str, Any]) -> EventEnvelope | None:
        try:
            msg_data = self._extract_data(data)
            if not msg_data or self._is_heartbeat_message(msg_data):
                return None

            weather_id = str(msg_data.get("id") or "").strip()
            if not weather_id:
                return None

            if self._is_weather_duplicate(weather_id):
                plugin_logger.info(
                    f"[灾害预警] {self.source_id} 检测到重复的气象预警ID: {weather_id}，忽略",
                    is_event_linked=True,
                    event_stream="weather_alarm",
                )
                return None

            title = str(msg_data.get("title") or "").strip()
            headline = str(msg_data.get("headline") or "").strip() or title
            description = str(msg_data.get("description") or "").strip()

            if not title and not headline and not description:
                return None

            origin_time_raw = msg_data.get("originTime")
            issue_time = _parse_timestamp_or_str(origin_time_raw) or datetime.now(timezone.utc)
            relieve_time = _parse_timestamp_or_str(msg_data.get("relieveTime"))
            weather_code = str(msg_data.get("type") or "").strip()

            raw_province = str(msg_data.get("province") or "").strip()
            raw_city = str(msg_data.get("city") or "").strip()
            raw_district = str(msg_data.get("district") or "").strip()
            raw_adcode = str(msg_data.get("adcode") or "").strip()

            province = None
            if raw_province:
                province = province_short(raw_province)
            if not province and raw_adcode:
                province = extract_province_from_adcode(raw_adcode)
            if not province and weather_id:
                province = extract_province_from_adcode(weather_id)
            if not province and title:
                province = resolve_province_from_text(title)
            if not province and headline:
                province = resolve_province_from_text(headline)

            source_entry = get_source_entry(self.source_id)
            metadata = {
                "issue_time": issue_time,
                "relieve_time": relieve_time,
                "weather_type": weather_code,
                "weather_code": weather_code,
                "type": weather_code,
                "province": province or "",
                "city": raw_city,
                "district": raw_district,
                "adcode": raw_adcode,
                "longitude": safe_float_convert(msg_data.get("longitude")),
                "latitude": safe_float_convert(msg_data.get("latitude")),
                "title": title,
                "headline": headline,
                "description": description,
                "source_family": "jian_project",
                "source_enum": source_entry.source_enum if source_entry else "jian_project_weather",
                "source_type": source_entry.source_type.value if source_entry else "weather",
            }

            domain_event = WeatherEvent(
                title=title,
                headline=headline,
                effective_at=issue_time,
                metadata=dict(metadata),
            )

            identity = EventIdentity(
                event_id=weather_id,
                source_id=self.source_id,
                event_type="weather_alarm",
                provider_family=source_entry.provider_family.value if source_entry else "jian_project",
                source_enum=source_entry.source_enum if source_entry else "jian_project_weather",
                published_at=issue_time,
                attributes={
                    "parser_name": self.source_entry.parser_name if self.source_entry else "weather_alarm_parser",
                    "config_key": source_entry.config_key if source_entry else "china_weather_alarm",
                },
            )

            envelope = EventEnvelope(
                identity=identity,
                event=domain_event,
                received_at=datetime.now(timezone.utc),
                payload=SourcePayload(
                    source_id=self.source_id,
                    provider_family=source_entry.provider_family.value if source_entry else "jian_project",
                    message_type="weather",
                    raw=dict(msg_data),
                    attributes=dict(metadata),
                ),
                metadata=metadata,
            )

            plugin_logger.info(
                f"[灾害预警] 气象预警解析成功: {domain_event.title or domain_event.headline}, 时间: {issue_time}",
                is_event_linked=True,
                event_stream="weather_alarm",
            )
            return envelope
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 解析气象数据失败: {exc}")
            return None


class ChinaTsunamiJianProjectParser(BaseParser):
    """自然资源部海啸预警中心海啸解析器 - Jian Project。"""

    def __init__(self, message_logger=None, source_id: str = "china_tsunami_jianproject"):
        super().__init__(source_id, message_logger)

    def _parse_data(self, data: dict[str, Any]) -> EventEnvelope | None:
        try:
            msg_data = self._extract_data(data)
            if not msg_data or self._is_heartbeat_message(msg_data):
                return None

            event_id = str(msg_data.get("id") or "").strip()
            title = str(msg_data.get("title") or msg_data.get("headline") or "").strip()
            level = str(msg_data.get("level") or "").strip()

            if not title and level:
                title = f"海啸{level}警报"
            if not title:
                return None

            issue_time = _parse_timestamp_or_str(msg_data.get("originTime")) or datetime.now(timezone.utc)
            source_entry = get_source_entry(self.source_id)

            metadata = {
                "code": event_id,
                "title": title,
                "level": level,
                "headline": str(msg_data.get("headline") or "").strip(),
                "number": msg_data.get("number", 1),
                "latitude": safe_float_convert(msg_data.get("latitude")),
                "longitude": safe_float_convert(msg_data.get("longitude")),
                "depth": safe_float_convert(msg_data.get("depth")),
                "magnitude": safe_float_convert(msg_data.get("magnitude")),
                "place_name": str(msg_data.get("place") or "").strip(),
                "org_unit": str(msg_data.get("orgUnit") or "自然资源部海啸预警中心").strip(),
                "description": str(msg_data.get("description") or "").strip(),
                "details_url": str(msg_data.get("htmlUrl") or "").strip(),
                "source_family": "jian_project",
                "source_enum": source_entry.source_enum if source_entry else "jian_project_tsunami",
                "source_type": source_entry.source_type.value if source_entry else "tsunami",
            }

            domain_event = TsunamiEvent(
                title=title,
                level=level,
                issued_at=issue_time,
                metadata=dict(metadata),
            )

            identity = EventIdentity(
                event_id=event_id or f"tsunami_jian_{int(issue_time.timestamp())}",
                source_id=self.source_id,
                event_type="tsunami",
                provider_family=source_entry.provider_family.value if source_entry else "jian_project",
                source_enum=source_entry.source_enum if source_entry else "jian_project_tsunami",
                published_at=issue_time,
                aliases=tuple(item for item in (event_id,) if item),
                attributes={
                    "parser_name": self.source_entry.parser_name if self.source_entry else "china_tsunami_parser",
                    "config_key": source_entry.config_key if source_entry else "china_tsunami",
                },
            )

            envelope = EventEnvelope(
                identity=identity,
                event=domain_event,
                received_at=datetime.now(timezone.utc),
                payload=SourcePayload(
                    source_id=self.source_id,
                    provider_family=source_entry.provider_family.value if source_entry else "jian_project",
                    message_type="nmefc-tsunami",
                    raw=dict(msg_data),
                    attributes=dict(metadata),
                ),
                metadata=metadata,
            )

            plugin_logger.info(
                f"[灾害预警] 海啸预警解析成功: {domain_event.title}, 等级: {domain_event.level}",
                is_event_linked=True,
                event_stream="tsunami",
            )
            return envelope
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 解析海啸数据失败: {exc}")
            return None


class CencEarthquakeJianProjectParser(BaseParser):
    """中国地震台网 (CENC) 地震测定解析器 - Jian Project。"""

    def __init__(self, message_logger=None, source_id: str = "cenc_jianproject"):
        super().__init__(source_id, message_logger)

    def _parse_data(self, data: dict[str, Any]) -> EventEnvelope | None:
        try:
            msg_data = self._extract_data(data)
            if not msg_data or self._is_heartbeat_message(msg_data):
                return None

            event_id = str(msg_data.get("id") or "").strip()
            if not event_id:
                return None

            occurred_at = _parse_timestamp_or_str(msg_data.get("originTime")) or datetime.now(timezone.utc)
            magnitude = safe_float_convert(msg_data.get("magnitude"))
            if magnitude is not None:
                magnitude = round(magnitude, 1)

            depth = safe_float_convert(msg_data.get("depth"))
            if depth is not None:
                depth = round(depth, 1)

            latitude = safe_float_convert(msg_data.get("latitude"))
            longitude = safe_float_convert(msg_data.get("longitude"))
            place_name = str(msg_data.get("placeName") or "").strip()
            info_type = str(msg_data.get("infoTypeName") or "").strip()

            source_entry = get_source_entry(self.source_id)
            metadata = {
                "source_family": "jian_project",
                "source_enum": source_entry.source_enum if source_entry else "jian_project_cenc",
                "source_type": source_entry.source_type.value if source_entry else "earthquake_info",
                "event_id": event_id,
                "info_type": info_type,
            }

            domain_event = EarthquakeEvent(
                occurred_at=occurred_at,
                latitude=latitude,
                longitude=longitude,
                place_name=place_name,
                magnitude=magnitude,
                depth=depth,
                metadata=dict(metadata),
            )

            identity = EventIdentity(
                event_id=event_id,
                source_id=self.source_id,
                event_type="earthquake",
                provider_family=source_entry.provider_family.value if source_entry else "jian_project",
                source_enum=source_entry.source_enum if source_entry else "jian_project_cenc",
                published_at=occurred_at,
                aliases=(event_id,),
                attributes={
                    "parser_name": self.source_entry.parser_name if self.source_entry else "china_report_parser",
                    "config_key": source_entry.config_key if source_entry else "china_cenc_earthquake",
                },
            )

            envelope = EventEnvelope(
                identity=identity,
                event=domain_event,
                received_at=datetime.now(timezone.utc),
                payload=SourcePayload(
                    source_id=self.source_id,
                    provider_family=source_entry.provider_family.value if source_entry else "jian_project",
                    message_type="cenc",
                    raw=dict(msg_data),
                    attributes=dict(metadata),
                ),
                metadata=metadata,
            )

            plugin_logger.info(
                f"[灾害预警] CENC 地震测定解析成功: {domain_event.place_name} (M {domain_event.magnitude}, {info_type})",
                is_event_linked=True,
                event_stream="earthquake",
            )
            return envelope
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 解析 CENC 数据失败: {exc}")
            return None


class UsgsEarthquakeJianProjectParser(BaseParser):
    """美国地质调查局 (USGS) 地震测定解析器 - Jian Project。"""

    def __init__(self, message_logger=None, source_id: str = "usgs_jianproject"):
        super().__init__(source_id, message_logger)

    def _parse_data(self, data: dict[str, Any]) -> EventEnvelope | None:
        try:
            msg_data = self._extract_data(data)
            if not msg_data or self._is_heartbeat_message(msg_data):
                return None

            event_id = str(msg_data.get("id") or "").strip()
            if not event_id:
                return None

            occurred_at = _parse_timestamp_or_str(msg_data.get("originTime")) or datetime.now(timezone.utc)
            magnitude = safe_float_convert(msg_data.get("magnitude"))
            if magnitude is not None:
                magnitude = round(magnitude, 1)

            depth = safe_float_convert(msg_data.get("depth"))
            if depth is not None:
                depth = round(depth, 1)

            latitude = safe_float_convert(msg_data.get("latitude")) or 0.0
            longitude = safe_float_convert(msg_data.get("longitude")) or 0.0
            raw_place_name = str(msg_data.get("placeName") or "").strip()
            info_type = str(msg_data.get("infoTypeName") or "").strip()

            # 翻译英文地名为中文
            place_name = region_service.translate_place_name(
                raw_place_name,
                latitude,
                longitude,
                fallback_to_original=True,
            )

            source_entry = get_source_entry(self.source_id)
            metadata = {
                "source_family": "jian_project",
                "source_enum": source_entry.source_enum if source_entry else "jian_project_usgs",
                "source_type": source_entry.source_type.value if source_entry else "earthquake_info",
                "event_id": event_id,
                "info_type": info_type,
            }

            domain_event = EarthquakeEvent(
                occurred_at=occurred_at,
                latitude=latitude,
                longitude=longitude,
                place_name=place_name,
                magnitude=magnitude,
                depth=depth,
                metadata=dict(metadata),
            )

            identity = EventIdentity(
                event_id=event_id,
                source_id=self.source_id,
                event_type="earthquake",
                provider_family=source_entry.provider_family.value if source_entry else "jian_project",
                source_enum=source_entry.source_enum if source_entry else "jian_project_usgs",
                published_at=occurred_at,
                aliases=(event_id,),
                attributes={
                    "parser_name": self.source_entry.parser_name if self.source_entry else "global_report_parser",
                    "config_key": source_entry.config_key if source_entry else "usgs_earthquake",
                },
            )

            envelope = EventEnvelope(
                identity=identity,
                event=domain_event,
                received_at=datetime.now(timezone.utc),
                payload=SourcePayload(
                    source_id=self.source_id,
                    provider_family=source_entry.provider_family.value if source_entry else "jian_project",
                    message_type="usgs",
                    raw=dict(msg_data),
                    attributes=dict(metadata),
                ),
                metadata=metadata,
            )

            plugin_logger.info(
                f"[灾害预警] USGS 地震测定解析成功: {domain_event.place_name} (M {domain_event.magnitude})",
                is_event_linked=True,
                event_stream="earthquake",
            )
            return envelope
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 解析 USGS 数据失败: {exc}")
            return None


__all__ = [
    "CeaEewJianProjectParser",
    "CwaEewJianProjectParser",
    "JmaEewJianProjectParser",
    "WeatherAlarmJianProjectParser",
    "ChinaTsunamiJianProjectParser",
    "CencEarthquakeJianProjectParser",
    "UsgsEarthquakeJianProjectParser",
]
