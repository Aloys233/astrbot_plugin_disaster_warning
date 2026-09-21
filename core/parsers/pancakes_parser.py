"""
PancakesAPI 专用解析器。
负责解析来自 PancakesAPI (wss://api.aloys23.link/api/v1/alert/ws/all) 聚合数据流中的
日本气象厅紧急地震速报 (jma_eew)、地震情报 (jma_eqlist) 与美国地质调查局 (usgs) 数据。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ...utils.converters import ScaleConverter, safe_float_convert
from ...utils.plugin_logger import plugin_logger
from ...utils.time_converter import TimeConverter
from ..domain.event_identity import EventIdentity
from ..domain.event_models import EarthquakeEvent, EventEnvelope
from ..domain.event_payload import SourcePayload
from ..services.geo.region_service import region_service
from ..sources.source_catalog import get_source_entry
from .base_parser import BaseParser


def _extract_payload_dict(data: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """提取内层业务载荷及外层元数据。

    返回 (payload_dict, outer_data)
    """
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            return {}, {}
    if not isinstance(data, dict):
        return {}, {}

    # 如果存在 payload 字段且为 dict，说明是标准的 RealtimeEvent 包装
    raw_payload = data.get("payload")
    if isinstance(raw_payload, dict):
        return raw_payload, data

    return data, data


class JmaEewPancakesParser(BaseParser):
    """日本气象厅紧急地震速报 (JMA EEW) 解析器 - PancakesAPI。"""

    def __init__(self, message_logger=None, source_id: str = "jma_pancakes"):
        super().__init__(source_id, message_logger)

    def _parse_data(self, data: dict[str, Any]) -> EventEnvelope | None:
        try:
            msg_data, outer = _extract_payload_dict(data)
            if not msg_data or self._is_heartbeat_message(msg_data):
                return None

            # 事件唯一 ID (如 20231114221320)
            event_id = str(
                msg_data.get("EventID")
                or msg_data.get("eventId")
                or msg_data.get("id")
                or ""
            ).strip()
            if not event_id:
                return None

            serial_raw = (
                msg_data.get("Serial")
                or msg_data.get("serial")
                or msg_data.get("report_num")
                or 1
            )
            try:
                report_num = int(serial_raw)
            except (ValueError, TypeError):
                report_num = 1

            title = str(
                msg_data.get("Title")
                or msg_data.get("title")
                or "緊急地震速報"
            ).strip()

            action = str(outer.get("action") or "").strip().lower()
            code_type = str(msg_data.get("CodeType") or "").strip()

            # 判断是否取消报 / 最终报 / 警报
            is_cancel = bool(
                action == "cancelled"
                or "取消" in title
                or code_type == "取消"
                or msg_data.get("isCancel") is True
            )
            is_final = bool(
                "最終" in title
                or action == "archived"
                or msg_data.get("isFinal") is True
            )
            is_warn = bool("警報" in title or msg_data.get("isWarn") is True)

            # 震中地名
            place_name = str(
                msg_data.get("Hypocenter")
                or msg_data.get("placeName")
                or msg_data.get("place_name")
                or ""
            ).strip()

            latitude = safe_float_convert(
                msg_data.get("Latitude") if msg_data.get("Latitude") is not None else msg_data.get("latitude")
            )
            longitude = safe_float_convert(
                msg_data.get("Longitude") if msg_data.get("Longitude") is not None else msg_data.get("longitude")
            )

            mag_val = (
                msg_data.get("Magunitude")
                if msg_data.get("Magunitude") is not None
                else (
                    msg_data.get("Magnitude")
                    if msg_data.get("Magnitude") is not None
                    else msg_data.get("magnitude")
                )
            )
            magnitude = safe_float_convert(mag_val)
            depth = safe_float_convert(
                msg_data.get("Depth") if msg_data.get("Depth") is not None else msg_data.get("depth")
            )

            # 最大预测烈度（震度）
            max_intensity = (
                msg_data.get("MaxIntensity")
                or msg_data.get("maxIntensity")
                or msg_data.get("intensity")
            )
            scale = ScaleConverter.parse_jma_cwa_scale(max_intensity)

            origin_time_raw = (
                msg_data.get("OriginTime")
                or msg_data.get("originTime")
                or msg_data.get("originTimeMs")
            )
            occurred_at = TimeConverter.parse_datetime(origin_time_raw) or datetime.now(timezone.utc)

            announced_time_raw = (
                msg_data.get("AnnouncedTime")
                or msg_data.get("announcedTime")
            )
            published_at = TimeConverter.parse_datetime(announced_time_raw) or occurred_at

            source_entry = get_source_entry(self.source_id)
            metadata = {
                "source_family": "global_quake",
                "source_enum": source_entry.source_enum if source_entry else "pancakes_jma_eew",
                "source_type": source_entry.source_type.value if source_entry else "earthquake_warning",
                "report_num": report_num,
                "is_final": is_final,
                "is_cancel": is_cancel,
                "is_warn": is_warn,
                "info_type": title,
                "origin_time": occurred_at,
                "announced_time": published_at,
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
                provider_family=source_entry.provider_family.value if source_entry else "global_quake",
                source_enum=source_entry.source_enum if source_entry else "pancakes_jma_eew",
                report_num=report_num,
                published_at=published_at,
                is_final=is_final,
                aliases=(event_id,),
                attributes={
                    "parser_name": self.source_entry.parser_name if self.source_entry else "jma_pancakes_parser",
                    "config_key": source_entry.config_key if source_entry else "japan_jma_eew",
                },
            )

            envelope = EventEnvelope(
                identity=identity,
                event=domain_event,
                received_at=datetime.now(timezone.utc),
                payload=SourcePayload(
                    source_id=self.source_id,
                    provider_family=source_entry.provider_family.value if source_entry else "global_quake",
                    message_type="jma_eew",
                    raw=dict(msg_data),
                    attributes=dict(metadata),
                ),
                metadata=metadata,
            )

            plugin_logger.info(
                f"[灾害预警] JMA 紧急地震速报 (Pancakes) 解析成功: {domain_event.place_name} "
                f"(M {domain_event.magnitude}, 震度 {domain_event.scale}) 第{report_num}报",
                is_event_linked=True,
                event_stream="earthquake",
            )
            return envelope
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 解析 JMA EEW 数据失败: {exc}")
            return None


class JmaEqlistPancakesParser(BaseParser):
    """日本气象厅地震速报列表 / 地震情报 (JMA EQLIST) 解析器 - PancakesAPI。"""

    def __init__(self, message_logger=None, source_id: str = "jma_eqlist_pancakes"):
        super().__init__(source_id, message_logger)

    def _parse_data(self, data: dict[str, Any]) -> EventEnvelope | None:
        try:
            msg_data, outer = _extract_payload_dict(data)
            if not msg_data or self._is_heartbeat_message(msg_data):
                return None

            event_id = str(
                msg_data.get("eventId")
                or msg_data.get("EventID")
                or msg_data.get("id")
                or ""
            ).strip()
            if not event_id:
                return None

            title = str(msg_data.get("title") or "地震情報").strip()
            telegram = str(msg_data.get("telegram") or "").strip()
            headline = str(msg_data.get("headline") or "").strip()

            action = str(outer.get("action") or "").strip().lower()
            status = str(msg_data.get("status") or "").strip()
            info_type = str(msg_data.get("infoType") or "").strip()

            is_cancel = bool(action == "cancelled" or status == "取消" or info_type == "取消")

            place_name = str(
                msg_data.get("placeName")
                or msg_data.get("place_name")
                or msg_data.get("Hypocenter")
                or ""
            ).strip()

            latitude = safe_float_convert(msg_data.get("latitude") if msg_data.get("latitude") is not None else msg_data.get("Latitude"))
            longitude = safe_float_convert(msg_data.get("longitude") if msg_data.get("longitude") is not None else msg_data.get("Longitude"))
            depth = safe_float_convert(msg_data.get("depth") if msg_data.get("depth") is not None else msg_data.get("Depth"))
            magnitude = safe_float_convert(msg_data.get("magnitude") if msg_data.get("magnitude") is not None else msg_data.get("Magnitude"))

            max_intensity = (
                msg_data.get("maxIntensity")
                or msg_data.get("MaxIntensity")
                or msg_data.get("intensity")
            )
            scale = ScaleConverter.parse_jma_cwa_scale(max_intensity)

            origin_time_raw = msg_data.get("originTime") or msg_data.get("OriginTime") or msg_data.get("originTimeMs")
            occurred_at = TimeConverter.parse_datetime(origin_time_raw) or datetime.now(timezone.utc)

            announced_time_raw = msg_data.get("announcedTime") or msg_data.get("reportTime") or msg_data.get("targetTime")
            published_at = TimeConverter.parse_datetime(announced_time_raw) or occurred_at

            source_entry = get_source_entry(self.source_id)
            metadata = {
                "source_family": "global_quake",
                "source_enum": source_entry.source_enum if source_entry else "pancakes_jma_eqlist",
                "source_type": source_entry.source_type.value if source_entry else "earthquake_info",
                "telegram": telegram,
                "title": title,
                "headline": headline,
                "status": status,
                "info_type": info_type or telegram,
                "is_cancel": is_cancel,
                "origin_time": occurred_at,
                "announced_time": published_at,
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
                event_type="earthquake",
                provider_family=source_entry.provider_family.value if source_entry else "global_quake",
                source_enum=source_entry.source_enum if source_entry else "pancakes_jma_eqlist",
                published_at=published_at,
                aliases=(event_id,),
                attributes={
                    "parser_name": self.source_entry.parser_name if self.source_entry else "jma_eqlist_pancakes_parser",
                    "config_key": source_entry.config_key if source_entry else "japan_jma_earthquake",
                },
            )

            envelope = EventEnvelope(
                identity=identity,
                event=domain_event,
                received_at=datetime.now(timezone.utc),
                payload=SourcePayload(
                    source_id=self.source_id,
                    provider_family=source_entry.provider_family.value if source_entry else "global_quake",
                    message_type="jma_eqlist",
                    raw=dict(msg_data),
                    attributes=dict(metadata),
                ),
                metadata=metadata,
            )

            plugin_logger.info(
                f"[灾害预警] JMA 地震情报 (Pancakes) 解析成功: {domain_event.place_name} "
                f"(M {domain_event.magnitude}, 震度 {domain_event.scale}, 电文 {telegram})",
                is_event_linked=True,
                event_stream="earthquake",
            )
            return envelope
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 解析 JMA 地震情报失败: {exc}")
            return None


class UsgsPancakesParser(BaseParser):
    """美国地质调查局 (USGS) 地震测定解析器 - PancakesAPI。"""

    def __init__(self, message_logger=None, source_id: str = "usgs_pancakes"):
        super().__init__(source_id, message_logger)

    def _parse_data(self, data: dict[str, Any]) -> EventEnvelope | None:
        try:
            msg_data, outer = _extract_payload_dict(data)
            if not msg_data or self._is_heartbeat_message(msg_data):
                return None

            event_id = str(
                msg_data.get("eventId")
                or msg_data.get("id")
                or ""
            ).strip()
            if not event_id:
                return None

            magnitude = safe_float_convert(msg_data.get("magnitude"))
            if magnitude is not None:
                magnitude = round(magnitude, 1)

            depth = safe_float_convert(msg_data.get("depth"))
            if depth is not None:
                depth = round(depth, 1)

            latitude = safe_float_convert(msg_data.get("latitude")) or 0.0
            longitude = safe_float_convert(msg_data.get("longitude")) or 0.0
            raw_place_name = str(msg_data.get("placeName") or msg_data.get("place_name") or "").strip()
            info_type = str(msg_data.get("infoType") or msg_data.get("infoTypeName") or "").strip()
            magnitude_type = str(msg_data.get("magnitudeType") or "").strip()
            url = str(msg_data.get("url") or "").strip()

            # 地名中英翻译
            place_name = region_service.translate_place_name(
                raw_place_name,
                latitude,
                longitude,
                fallback_to_original=True,
            )

            origin_time_raw = msg_data.get("originTimeMs") or msg_data.get("originTimeIso") or msg_data.get("originTime")
            occurred_at = TimeConverter.parse_datetime(origin_time_raw) or datetime.now(timezone.utc)

            updated_time_raw = msg_data.get("updatedTimeMs") or msg_data.get("updatedTimeIso") or msg_data.get("updatedTime")
            published_at = TimeConverter.parse_datetime(updated_time_raw) or occurred_at

            source_entry = get_source_entry(self.source_id)
            metadata = {
                "source_family": "global_quake",
                "source_enum": source_entry.source_enum if source_entry else "pancakes_usgs",
                "source_type": source_entry.source_type.value if source_entry else "earthquake_info",
                "event_id": event_id,
                "info_type": info_type,
                "magnitude_type": magnitude_type,
                "url": url,
                "origin_place_en": raw_place_name,
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
                provider_family=source_entry.provider_family.value if source_entry else "global_quake",
                source_enum=source_entry.source_enum if source_entry else "pancakes_usgs",
                published_at=published_at,
                aliases=(event_id,),
                attributes={
                    "parser_name": self.source_entry.parser_name if self.source_entry else "usgs_pancakes_parser",
                    "config_key": source_entry.config_key if source_entry else "usgs_earthquake",
                },
            )

            envelope = EventEnvelope(
                identity=identity,
                event=domain_event,
                received_at=datetime.now(timezone.utc),
                payload=SourcePayload(
                    source_id=self.source_id,
                    provider_family=source_entry.provider_family.value if source_entry else "global_quake",
                    message_type="usgs",
                    raw=dict(msg_data),
                    attributes=dict(metadata),
                ),
                metadata=metadata,
            )

            plugin_logger.info(
                f"[灾害预警] USGS 地震测定 (Pancakes) 解析成功: {domain_event.place_name} (M {domain_event.magnitude})",
                is_event_linked=True,
                event_stream="earthquake",
            )
            return envelope
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 解析 USGS 数据失败: {exc}")
            return None


__all__ = [
    "JmaEewPancakesParser",
    "JmaEqlistPancakesParser",
    "UsgsPancakesParser",
]
