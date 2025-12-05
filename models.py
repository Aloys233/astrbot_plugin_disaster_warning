"""
灾害预警数据模型
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class DisasterType(Enum):
    """灾害类型"""

    EARTHQUAKE = "earthquake"
    EARTHQUAKE_WARNING = "earthquake_warning"
    TSUNAMI = "tsunami"
    WEATHER_ALARM = "weather_alarm"


class DataSource(Enum):
    """数据源类型"""

    FAN_STUDIO_CENC = "fan_studio_cenc"  # 中国地震台网
    FAN_STUDIO_CEA = "fan_studio_cea"  # 中国地震预警网
    FAN_STUDIO_CWA = "fan_studio_cwa"  # 台湾中央气象署
    FAN_STUDIO_USGS = "fan_studio_usgs"  # USGS
    FAN_STUDIO_WEATHER = "fan_studio_weather"  # 中国气象局气象预警
    FAN_STUDIO_TSUNAMI = "fan_studio_tsunami"  # 海啸预警
    P2P_EEW = "p2p_eew"  # P2P地震情報緊急地震速報
    P2P_EARTHQUAKE = "p2p_earthquake"  # P2P地震情報
    WOLFX_JMA_EEW = "wolfx_jma_eew"  # Wolfx日本气象厅紧急地震速报
    WOLFX_CENC_EEW = "wolfx_cenc_eew"  # Wolfx中国地震台网预警
    WOLFX_CWA_EEW = "wolfx_cwa_eew"  # Wolfx台湾地震预警
    GLOBAL_QUAKE = "global_quake"  # Global Quake服务器


@dataclass
class EarthquakeData:
    """地震数据"""

    id: str
    event_id: str
    source: DataSource
    disaster_type: DisasterType

    # 基本信息
    shock_time: datetime
    latitude: float
    longitude: float

    # 位置信息
    place_name: str

    # 有默认值的字段（必须放在后面）
    depth: float | None = None
    magnitude: float | None = None

    # 烈度/震度信息
    intensity: float | None = None  # 中国烈度
    scale: float | None = None  # 日本震度
    max_intensity: float | None = None  # 最大烈度/震度
    max_scale: float | None = None  # P2P数据源的最大震度值

    # 位置信息
    province: str | None = None

    # 更新信息
    updates: int = 1
    is_final: bool = False
    is_cancel: bool = False

    # 其他信息
    info_type: str = ""  # 测定类型：自动/正式等
    domestic_tsunami: str | None = None
    foreign_tsunami: str | None = None

    # 时间信息（用于不同数据源）
    update_time: datetime | None = None  # 更新时间（USGS等数据源）
    create_time: datetime | None = None  # 创建时间（CWA等数据源）

    # 原始数据
    raw_data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if isinstance(self.shock_time, str):
            self.shock_time = datetime.fromisoformat(
                self.shock_time.replace("Z", "+00:00")
            )


@dataclass
class TsunamiData:
    """海啸数据"""

    id: str
    code: str
    source: DataSource
    title: str
    level: str  # 黄色、橙色、红色、解除

    # 默认值的字段（必须放在后面）
    disaster_type: DisasterType = DisasterType.TSUNAMI
    subtitle: str | None = None
    org_unit: str = ""

    # 时间信息
    issue_time: datetime | None = None

    # 预报区域
    forecasts: list[dict[str, Any]] = field(default_factory=list)

    # 监测站信息
    monitoring_stations: list[dict[str, Any]] = field(default_factory=list)

    # 原始数据
    raw_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class WeatherAlarmData:
    """气象预警数据"""

    id: str
    source: DataSource
    headline: str
    title: str
    description: str
    type: str  # 预警类型编码
    effective_time: datetime

    # 默认值的字段
    disaster_type: DisasterType = DisasterType.WEATHER_ALARM
    issue_time: datetime | None = None
    longitude: float | None = None
    latitude: float | None = None
    raw_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class DisasterEvent:
    """统一灾害事件格式"""

    id: str
    data: Any  # EarthquakeData, TsunamiData, WeatherAlarmData
    source: DataSource
    disaster_type: DisasterType
    receive_time: datetime = field(default_factory=datetime.now)
