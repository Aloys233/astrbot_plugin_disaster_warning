"""
WebSocket连接管理器
"""

import asyncio
from collections.abc import Callable
from typing import Any

import aiohttp
import websockets

from astrbot.api import logger


class WebSocketManager:
    """WebSocket连接管理器"""

    def __init__(self, config: dict[str, Any], message_logger=None):
        self.config = config
        self.message_logger = message_logger
        self.connections: dict[str, websockets.WebSocketServerProtocol] = {}
        self.message_handlers: dict[str, Callable] = {}
        self.reconnect_tasks: dict[str, asyncio.Task] = {}
        self.connection_retry_counts: dict[str, int] = {}  # 记录每个连接的重试次数
        self.running = False

    def register_handler(self, connection_name: str, handler: Callable):
        """注册消息处理器"""
        self.message_handlers[connection_name] = handler

    async def connect(
        self,
        name: str,
        uri: str,
        headers: dict | None = None,
        is_retry: bool = False,
    ):
        """建立WebSocket连接"""
        try:
            # 如果是重试连接，记录重试次数
            if is_retry:
                current_retry = self.connection_retry_counts.get(name, 0) + 1
                self.connection_retry_counts[name] = current_retry
                max_retries = self.config.get("max_reconnect_retries", 3)
                logger.info(
                    f"[灾害预警] 尝试重连 {name} (尝试 {current_retry}/{max_retries})"
                )
            else:
                logger.info(f"[灾害预警] 正在连接 {name}: {uri}")
                # 首次连接时重置重试计数
                self.connection_retry_counts[name] = 0

            # 修复：使用更兼容的headers参数
            connect_kwargs = {
                "uri": uri,
                "ping_interval": self.config.get("heartbeat_interval", 60),
                "ping_timeout": self.config.get("connection_timeout", 10),
            }

            # 只有在有headers时才添加
            if headers:
                connect_kwargs["headers"] = headers

            async with websockets.connect(**connect_kwargs) as websocket:
                self.connections[name] = websocket
                logger.info(f"[灾害预警] WebSocket连接成功: {name}")

                # 处理消息
                async for message in websocket:
                    try:
                        # 记录原始消息 - 在处理器查找之前记录，确保所有消息都被记录
                        if self.message_logger:
                            self.message_logger.log_websocket_message(
                                name, message, uri
                            )

                        # 智能处理器查找（支持前缀匹配）
                        if name in self.message_handlers:
                            handler_name = name
                        else:
                            # 尝试前缀匹配（解决 fan_studio_usgs -> fan_studio 问题）
                            handler_name = self._find_handler_by_prefix(name)

                        if handler_name:
                            # 关键修复：传递连接名称给处理器，确保source信息正确
                            await self.message_handlers[handler_name](
                                message, connection_name=name
                            )
                        else:
                            logger.warning(
                                f"[灾害预警] 未找到消息处理器 - 连接: {name}"
                            )
                    except Exception as e:
                        logger.error(f"[灾害预警] 处理消息时出错 {name}: {e}")
                        import traceback

                        logger.error(f"[灾害预警] 异常堆栈: {traceback.format_exc()}")

        except Exception as e:
            # 更详细的错误分析和日志
            error_msg = str(e)
            if "1012" in error_msg and "service restart" in error_msg:
                logger.warning(
                    f"[灾害预警] WebSocket连接收到服务重启通知 {name}: {error_msg}"
                )
                logger.info(f"[灾害预警] {name} 服务器正在重启，将在稍后自动重连")
            elif "HTTP 502" in error_msg:
                logger.warning(
                    f"[灾害预警] WebSocket服务器网关错误 {name}: {error_msg}"
                )
                logger.info(f"[灾害预警] {name} 服务器可能正在维护，将稍后重试")
            elif "connection refused" in error_msg.lower():
                logger.warning(f"[灾害预警] WebSocket连接被拒绝 {name}: {error_msg}")
                logger.info(f"[灾害预警] {name} 服务器可能暂时不可用")
            else:
                logger.error(f"[灾害预警] WebSocket连接失败 {name}: {error_msg}")

            self.connections.pop(name, None)

            # 启动重连任务
            if self.running:
                await self._schedule_reconnect(name, uri, headers)

    async def _schedule_reconnect(
        self, name: str, uri: str, headers: dict | None = None
    ):
        """计划重连"""
        if name in self.reconnect_tasks:
            self.reconnect_tasks[name].cancel()

        async def reconnect():
            # 获取当前重试次数和最大重试次数
            current_retry = self.connection_retry_counts.get(name, 0)
            max_retries = self.config.get("max_reconnect_retries", 3)

            # 检查是否已达到最大重试次数
            if current_retry >= max_retries:
                logger.error(
                    f"[灾害预警] {name} 重连失败，已达到最大重试次数 {max_retries}，将停止重连"
                )
                return

            try:
                await asyncio.sleep(self.config.get("reconnect_interval", 30))
                # 标记为重试连接
                await self.connect(name, uri, headers, is_retry=True)
            except Exception as e:
                logger.error(f"[灾害预警] 重连失败 {name}: {e}")
                # 如果还有重试次数，继续安排重连
                if self.connection_retry_counts.get(name, 0) < max_retries:
                    await self._schedule_reconnect(name, uri, headers)

        self.reconnect_tasks[name] = asyncio.create_task(reconnect())

    async def disconnect(self, name: str):
        """断开连接"""
        if name in self.connections:
            try:
                await self.connections[name].close()
            except Exception as e:
                logger.error(f"[灾害预警] 断开连接时出错 {name}: {e}")
            finally:
                self.connections.pop(name, None)

        if name in self.reconnect_tasks:
            self.reconnect_tasks[name].cancel()
            self.reconnect_tasks.pop(name, None)

    async def send_message(self, name: str, message: str):
        """发送消息"""
        if name in self.connections:
            try:
                await self.connections[name].send(message)
            except Exception as e:
                logger.error(f"[灾害预警] 发送消息失败 {name}: {e}")

    async def start(self):
        """启动管理器"""
        self.running = True
        logger.info("[灾害预警] WebSocket管理器已启动")

    async def stop(self):
        """停止管理器"""
        self.running = False

        # 取消所有重连任务
        for task in self.reconnect_tasks.values():
            task.cancel()

        # 断开所有连接
        for name in list(self.connections.keys()):
            await self.disconnect(name)

        logger.info("[灾害预警] WebSocket管理器已停止")

    def _find_handler_by_prefix(self, connection_name: str) -> str | None:
        """通过前缀匹配查找处理器名称"""
        # 定义连接名称前缀到处理器名称的映射
        prefix_mappings = {
            "fan_studio_": "fan_studio",  # fan_studio_usgs -> fan_studio
            "p2p_": "p2p",  # p2p_main -> p2p
            "wolfx_": "wolfx",  # wolfx_japan_jma_eew -> wolfx
        }

        # 尝试前缀匹配
        for prefix, handler_name in prefix_mappings.items():
            if connection_name.startswith(prefix):
                # 验证处理器确实存在
                if handler_name in self.message_handlers:
                    return handler_name
                else:
                    logger.warning(
                        f"[灾害预警] 前缀匹配找到但处理器不存在: '{connection_name}' -> '{handler_name}'"
                    )

        # 如果没有找到匹配，尝试更宽松的前缀匹配
        for handler_name in self.message_handlers.keys():
            if connection_name.startswith(handler_name):
                return handler_name

        return None


class HTTPDataFetcher:
    """HTTP数据获取器"""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.config.get("http_timeout", 30))
        )
        return self

    async def __aexit__(self, exc_type=None, exc_val=None, exc_tb=None):
        # 忽略异常参数，仅用于资源清理
        _ = exc_type, exc_val, exc_tb  # 抑制未使用变量警告
        if self.session:
            await self.session.close()

    async def fetch_json(self, url: str, headers: dict | None = None) -> dict | None:
        """获取JSON数据"""
        if not self.session:
            return None

        try:
            async with self.session.get(url, headers=headers) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    logger.warning(f"[灾害预警] HTTP请求失败 {url}: {response.status}")
        except Exception as e:
            logger.error(f"[灾害预警] HTTP请求异常 {url}: {e}")

        return None


class GlobalQuakeClient:
    """Global Quake TCP客户端"""

    def __init__(self, config: dict[str, Any], message_logger=None):
        self.config = config
        self.message_logger = message_logger
        # 关键修复：确保服务器地址是字符串，而不是布尔值
        primary_server = config.get("primary_server", "server-backup.globalquake.net")
        secondary_server = config.get(
            "secondary_server", "server-backup.globalquake.net"
        )

        # 处理布尔值配置问题
        if isinstance(primary_server, bool):
            primary_server = "server-backup.globalquake.net" if primary_server else ""
        if isinstance(secondary_server, bool):
            secondary_server = (
                "server-backup.globalquake.net" if secondary_server else ""
            )

        # 确保是有效的字符串地址
        self.primary_server = (
            str(primary_server)
            if isinstance(primary_server, str) and primary_server.strip()
            else "server-backup.globalquake.net"
        )
        self.secondary_server = (
            str(secondary_server)
            if isinstance(secondary_server, str) and secondary_server.strip()
            else "server-backup.globalquake.net"
        )
        self.primary_port = config.get("primary_port", 38000)
        self.secondary_port = config.get("secondary_port", 38000)

        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.running = False
        self.message_handler: Callable | None = None

    def register_handler(self, handler: Callable):
        """注册消息处理器"""
        self.message_handler = handler

    async def connect(self):
        """连接到Global Quake服务器"""
        servers = [
            (self.primary_server, self.primary_port),
            (self.secondary_server, self.secondary_port),
        ]

        for server, port in servers:
            try:
                logger.info(f"[灾害预警] 正在连接Global Quake服务器 {server}:{port}")
                self.reader, self.writer = await asyncio.open_connection(server, port)
                logger.info(f"[灾害预警] Global Quake连接成功: {server}:{port}")
                return True
            except Exception as e:
                logger.error(f"[灾害预警] Global Quake连接失败 {server}:{port}: {e}")

        return False

    async def listen(self):
        """监听消息"""
        if not self.reader or not self.writer:
            return

        self.running = True

        try:
            while self.running:
                data = await self.reader.readline()
                if not data:
                    break

                message = data.decode("utf-8").strip()
                if message and self.message_handler:
                    try:
                        # 添加Global Quake消息调试日志
                        logger.debug(
                            f"[灾害预警] Global Quake收到原始消息: {message[:100]}..."
                        )

                        # 记录原始消息
                        if self.message_logger:
                            current_server = (
                                self.writer.get_extra_info("peername")[0]
                                if self.writer
                                else "unknown"
                            )
                            current_port = (
                                self.writer.get_extra_info("peername")[1]
                                if self.writer
                                else 0
                            )
                            logger.debug(
                                f"[灾害预警] 准备记录Global Quake TCP消息 - 服务器: {current_server}:{current_port}"
                            )
                            self.message_logger.log_tcp_message(
                                current_server, current_port, message
                            )
                            logger.debug("[灾害预警] Global Quake TCP消息记录完成")
                        else:
                            logger.debug(
                                "[灾害预警] 消息记录器未启用，无法记录Global Quake消息"
                            )

                        await self.message_handler(message)
                    except Exception as e:
                        logger.error(f"[灾害预警] 处理Global Quake消息时出错: {e}")

        except asyncio.CancelledError:
            logger.info("[灾害预警] Global Quake监听任务被取消")
        except Exception as e:
            logger.error(f"[灾害预警] Global Quake监听异常: {e}")
        finally:
            await self.disconnect()

    async def disconnect(self):
        """断开连接"""
        self.running = False

        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception as e:
                logger.error(f"[灾害预警] 断开Global Quake连接时出错: {e}")
            finally:
                self.writer = None
                self.reader = None

    async def send_message(self, message: str):
        """发送消息"""
        if self.writer:
            try:
                self.writer.write(message.encode("utf-8"))
                await self.writer.drain()
            except Exception as e:
                logger.error(f"[灾害预警] 发送Global Quake消息失败: {e}")
