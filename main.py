import asyncio
import os
import dataclasses
import uuid
import logging
import pathlib
import enum
import contextlib

from dotenv import load_dotenv
from fastapi.websockets import WebSocket, WebSocketState, WebSocketDisconnect
from fastapi import FastAPI
from fastapi.responses import FileResponse
from redis import asyncio as aioredis


root = pathlib.Path(__file__).parent
filepath = root / "local.env"
index_html = root / "index.html"


if not index_html.exists():
    raise FileNotFoundError("index.html file not found")

if not filepath.exists():
    raise FileNotFoundError("local.env file not found")


load_dotenv(dotenv_path=filepath)
REDIS_URL = os.getenv(
    "REDIS_URL", "redis://localhost:6379/0"
)
FORCE_SHUTDOWN_TIMEOUT = int(os.getenv("FORCE_SHUTDOWN_TIMEOUT"))
REPORT_PROGRESS_INTERVAL = int(os.getenv("REPORT_PROGRESS_INTERVAL"))
DEBUG_CLEANUP_DELAY = int(os.getenv("DEBUG_CLEANUP_DELAY"))
QUEUE_NAME = os.getenv("QUEUE_NAME") or "websocket_broadcast"
logger = logging.getLogger("uvicorn.error")


@enum.unique
class CommandType(enum.Enum):
    BROADCAST = "broadcast"
    MESSAGE = "message"


@dataclasses.dataclass
class Message:
    command: CommandType
    content: str

    def to_message_string(self) -> str:
        return f"{self.command.value}:{self.content}"

    @classmethod
    def from_message_string(cls, message_string: str) -> "Message":
        try:
            command_str, content = message_string.split(":", 1)
            command = CommandType(command_str)
            return cls(command, content)

        except (ValueError, KeyError) as e:
            raise ValueError(f"Invalid message string: {message_string}") from e


@dataclasses.dataclass
class WebsocketInfo:
    websocket: WebSocket
    weaksocket_event: asyncio.Event


class ConnectionManager:

    def __init__(self):
        self._active_connections: dict[uuid.UUID, WebsocketInfo] = {}

    async def connect(self, websocket: WebSocket) -> tuple[asyncio.Event, uuid.UUID]:
        await websocket.accept()
        weaksocket_event = asyncio.Event()
        connection_id = uuid.uuid4()
        self._active_connections[connection_id] = WebsocketInfo(websocket, weaksocket_event)
        return weaksocket_event, connection_id

    async def disconnect(self, uuid_key: uuid.UUID) -> None:
        websocket_info = self._active_connections.pop(uuid_key, None)

        if websocket_info:
            websocket_info.weaksocket_event.set()
            if not websocket_info.websocket.client_state == WebSocketState.DISCONNECTED:
                await websocket_info.websocket.close()

    async def broadcast(self, message: str) -> None:
        for connection in self._active_connections.values():
            websocket = connection.websocket

            if websocket.client_state == WebSocketState.CONNECTED:
                await connection.websocket.send_text(message)

            elif not connection.weaksocket_event.is_set():
                connection.weaksocket_event.set()

    def get_active_connection_count(self) -> int:
        return len(self._active_connections)
    
    async def disconnect_all(self) -> None:
        disconnect_tasks = [
            self.disconnect(connection_id)
            for connection_id in list(self._active_connections.keys())
        ]
        await asyncio.gather(*disconnect_tasks, return_exceptions=True)


manager = ConnectionManager()
redis = aioredis.from_url(REDIS_URL)


async def setup_shutdown():
    logger.info("Starting shutdown procedure...")
    await asyncio.sleep(DEBUG_CLEANUP_DELAY)  # Simulate some cleanup delay
    await manager.disconnect_all()
    logger.info("All connections have been disconnected.")


async def setup_report_progress(stop_event: asyncio.Event) -> None:
    start_time = asyncio.get_event_loop().time()
    while not stop_event.is_set():
        connections = manager.get_active_connection_count()
        time_to_force_shutdown = FORCE_SHUTDOWN_TIMEOUT - (asyncio.get_event_loop().time() - start_time)
        logger.info(f"Active connections: {connections} / Time to force shutdown: {time_to_force_shutdown:.2f} seconds")
        await asyncio.sleep(REPORT_PROGRESS_INTERVAL) 


async def subscribe_to_redis(
    redis: aioredis.Redis,
    stop_event: asyncio.Event
) -> None:
    pubsub = redis.pubsub()
    await pubsub.subscribe(QUEUE_NAME)

    try:
        while not stop_event.is_set():
            message = await asyncio.wait_for(
                pubsub.get_message(ignore_subscribe_messages=True),
                timeout=0.1,
            )
            if message:
                await manager.broadcast(message['data'].decode('utf-8'))
    except asyncio.CancelledError:
        pass

    finally:
        await pubsub.unsubscribe(QUEUE_NAME)
        await pubsub.close()


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):

    try:

        logger.info("Starting Redis subscriber task...")
        stop_event = asyncio.Event()
        redis_subscription = asyncio.create_task(
            subscribe_to_redis(redis, stop_event)
        )

        yield

        logger.info("Shutting down server...")

        stop_event.set()
        await redis_subscription

        report_progress_stop_event = asyncio.Event()
        report_task = asyncio.create_task(
            setup_report_progress(report_progress_stop_event)
        )

        try:
            await asyncio.wait_for(
                setup_shutdown(), 
                timeout=FORCE_SHUTDOWN_TIMEOUT,
            )

        except asyncio.TimeoutError:
            report_progress_stop_event.set()
            await report_task
            logger.warning("Shutdown timed out, forcing exit.")
            os._exit(0)

        finally:
            report_progress_stop_event.set()
            await report_task
            logger.info("Server shutdown complete.")

    except Exception as e:
        logger.error(f"Error in lifespan management: {e}")


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def root():
    return FileResponse(index_html)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    weaksocket_event, connection_id = await manager.connect(websocket)

    try:
        while not weaksocket_event.is_set():
            data = await websocket.receive_text()
            message = Message.from_message_string(data)

            if message.command == CommandType.BROADCAST:
                await redis.publish(QUEUE_NAME, message.to_message_string())

            if message.command == CommandType.MESSAGE:
                await websocket.send_text(Message(
                    command=CommandType.MESSAGE,
                    content=f"Echo: {message.content}"
                ).to_message_string())

    except WebSocketDisconnect:
        logger.info(f"Connection {connection_id} disconnected")

    except Exception as e:
        logger.error(f"Error in connection {connection_id}: {e}")

    finally:
        await manager.disconnect(connection_id)
        logger.info(f"Connection {connection_id} cleanup complete")

