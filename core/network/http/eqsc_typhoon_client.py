"""
EQSC 台风 HTTP 客户端。

负责向 EQSC API 发送台风数据查询请求，并维护简单的内存缓存以减少重复请求。
公共鉴权 / 会话 / 日志能力由 EqscHttpClient 提供。
"""

from __future__ import annotations

import time
from typing import Any

from astrbot.api import logger

from .eqsc_http_client import EqscHttpClient
from .eqsc_token_manager import EqscTokenManager


class EqscTyphoonClient(EqscHttpClient):
    """EQSC 台风数据 HTTP 客户端。"""

    def __init__(
        self,
        token_manager: EqscTokenManager,
        config: dict[str, Any],
        message_logger: Any | None = None,
        *,
        owns_token_manager: bool = True,
    ):
        """初始化台风客户端。

        Args:
            token_manager: EQSC 令牌管理器实例。
            config: EQSC 配置字典（缓存 TTL 已硬编码，不再读取 cache_ttl）。
            message_logger: 可选原始消息记录器；启用后会落盘 EQSC HTTP 响应。
            owns_token_manager: close() 时是否关闭 token_manager。
        """
        super().__init__(
            token_manager,
            config,
            message_logger=message_logger,
            owns_token_manager=owns_token_manager,
            # cache_ttl 配置项已从配置契约移除：硬编码 300 秒缓存，
            # 传入 None 完全禁用配置读取，避免任何残留键覆盖默认值。
            default_cache_ttl=300,
            cache_ttl_config_key=None,
        )
        # 缓存结构: {typhoon_id: (data, expires_at)}
        self._cache: dict[str, tuple[dict[str, Any], float]] = {}
        # 无参查询缓存（全部最新台风列表）
        self._list_cache: tuple[list[dict[str, Any]], float] | None = None
        # 最近一次查询结果状态：hit（命中）/ empty（未命中）/ error（异常）。
        # 供富化服务区分「编号不存在」与「服务故障」，未命中直接回退而非退避重试。
        self._last_lookup_status: str = "empty"

    @property
    def last_lookup_status(self) -> str:
        """最近一次台风查询结果状态：hit / empty / error。

        - hit：成功拿到数据；
        - empty：接口正常返回但未命中（编号不存在或名称不匹配）；
        - error：网络 / 鉴权等可恢复异常。

        富化服务据此区分未命中与故障，避免对不存在的编号反复退避重试。
        """
        return getattr(self, "_last_lookup_status", "empty")

    def clear_cache(self) -> None:
        """清除所有缓存。"""
        self._cache.clear()
        self._list_cache = None

    async def fetch_typhoon_by_id(
        self,
        typhoon_id: str,
        access_token: str | None = None,
        *,
        use_cache: bool = True,
    ) -> dict[str, Any] | None:
        """按台风 ID 查询台风详细数据。

        EQSC 的台风 ID 格式为 4 位（年份后2位+编号2位），
        FAN Studio 的台风 ID 格式为 6 位（年份4位+编号2位），
        调用方需在传入前完成 ID 转换。

        Args:
            typhoon_id: EQSC 格式的台风 ID（4位）。
            access_token: 可复用的 AccessToken；若未提供则内部自行获取。
            use_cache: 为 False 时强制绕过详情缓存（查询指令侧使用）。

        Returns:
            台风数据字典，或 None 表示查询失败/未找到。
        """
        # 每次查询前重置状态，避免上一次结果污染本次判定
        self._last_lookup_status = "empty"

        # 检查缓存
        if use_cache:
            cached = self._cache.get(typhoon_id)
            if cached and self._is_cache_valid(cached[1]):
                self._last_lookup_status = "hit"
                return cached[0]

        # 获取 AccessToken
        access_token = await self._resolve_access_token(access_token)
        if not access_token:
            # 无可用令牌属通道级异常，标记为 error 交由上层走重试/熔断逻辑
            self._last_lookup_status = "error"
            return None

        try:
            url = f"{self._base_url}/typhoonNMC.json"
            status, data, _raw = await self._request_json(
                url=url,
                access_token=access_token,
                params={"id": typhoon_id},
                log_label=f"EQSC 查询台风 {typhoon_id}",
                # EQSC 用 HTTP 500 表示「编号不存在」，属正常未命中，
                # 降级为 INFO 避免刷 WARN 噪音（其它 EQSC 接口行为不变）。
                failure_log_level="info",
            )
            if status != 200 or not isinstance(data, dict):
                # EQSC 对不存在的编号返回 500，此处保持 empty 语义（未命中），
                # 由上层结合列表查询结果决定是否继续重试。
                return None

            # 解析响应：{"typhoon": [{...}]}
            typhoon_list = data.get("typhoon", []) if isinstance(data, dict) else []
            if not typhoon_list:
                logger.debug(f"[灾害预警] EQSC 台风 {typhoon_id} 未找到匹配数据")
                return None

            # 取第一个匹配的台风
            typhoon_data = typhoon_list[0]
            # 写入缓存
            self._cache[typhoon_id] = (typhoon_data, time.time() + self._cache_ttl)
            self._last_lookup_status = "hit"
            return typhoon_data

        except Exception as e:
            self._last_lookup_status = "error"
            logger.error(
                f"[灾害预警] EQSC 查询台风 {typhoon_id} 异常: "
                f"{type(e).__name__}: {str(e) or repr(e)}"
            )
            return None

    async def fetch_typhoon_list(
        self,
        access_token: str | None = None,
        *,
        use_cache: bool = True,
    ) -> list[dict[str, Any]]:
        """查询 EQSC 台风列表（无参，至多约 20 个最新台风，含历史）。

        注意：该接口并非严格“仅活跃台风”，实际常返回最新历史编报集合。

        Args:
            use_cache: 为 False 时强制绕过列表缓存（轮询侧使用）。
        """
        # 检查缓存
        if use_cache and self._list_cache and self._is_cache_valid(self._list_cache[1]):
            # 列表接口成功返回（含空列表）均视为通道正常：empty 表示服务可用但未命中。
            self._last_lookup_status = "hit" if self._list_cache[0] else "empty"
            return self._list_cache[0]

        # 获取 AccessToken
        access_token = await self._resolve_access_token(access_token)
        if not access_token:
            self._last_lookup_status = "error"
            return []

        try:
            url = f"{self._base_url}/typhoonNMC.json"
            status, data, _raw = await self._request_json(
                url=url,
                access_token=access_token,
                log_label="EQSC 查询台风列表",
            )
            if status != 200 or not isinstance(data, dict):
                # 列表接口异常：标记为 error，供上层区分网络故障与编号未命中。
                self._last_lookup_status = "error"
                return []

            typhoon_list = data.get("typhoon", []) if isinstance(data, dict) else []
            # 写入缓存
            self._list_cache = (typhoon_list, time.time() + self._cache_ttl)
            # 接口调用成功：列表非空为 hit，空列表为 empty（通道正常但无数据）。
            self._last_lookup_status = "hit" if typhoon_list else "empty"
            return typhoon_list

        except Exception as e:
            self._last_lookup_status = "error"
            error_name = type(e).__name__
            # DNS 解析失败（getaddrinfo failed）等连接层错误对普通用户是黑话，
            # 先给出人性化提示，再附带原始技术细节便于排障。
            # 注意：DNS 失败常以 socket.gaierror 作为 __cause__ 被 ClientConnectorError
            # 包装，或作为 __context__ 关联，因此需遍历整条异常链判断，而非只看顶层类型名。
            if self._is_dns_error(e):
                logger.error(
                    f"[灾害预警] EQSC 查询台风列表失败：无法解析域名 {self._base_url}，"
                    f"请检查网络或 DNS 配置（原始错误: {error_name}: {str(e) or repr(e)}）"
                )
            else:
                logger.error(
                    f"[灾害预警] EQSC 查询台风列表异常: {error_name}: {str(e) or repr(e)}"
                )
            return []

    def find_typhoon_by_name(
        self,
        typhoon_list: list[dict[str, Any]],
        name_cn: str = "",
        name_en: str = "",
    ) -> dict[str, Any] | None:
        """在台风列表中按名称匹配台风。

        Args:
            typhoon_list: EQSC 返回的台风列表。
            name_cn: 台风中文名。
            name_en: 台风英文名。

        Returns:
            匹配到的台风数据字典，或 None。
        """
        for typhoon in typhoon_list:
            if not isinstance(typhoon, dict):
                continue
            eqsc_name_cn = str(typhoon.get("nameCN", "") or "").strip()
            eqsc_name_en = str(typhoon.get("nameEN", "") or "").strip()
            if name_cn and eqsc_name_cn and name_cn == eqsc_name_cn:
                return typhoon
            if name_en and eqsc_name_en and name_en.upper() == eqsc_name_en.upper():
                return typhoon
        return None


__all__ = ["EqscTyphoonClient"]
