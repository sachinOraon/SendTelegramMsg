import logging
import os
import asyncio
from pyrogram import Client, errors, enums, idle
from http import HTTPStatus
from typing import Optional
from dotenv import load_dotenv
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type, RetryError
from sys import exit
from aiohttp import web, ClientSession, ClientTimeout
from aiohttp_middlewares import cors_middleware
from aiohttp_middlewares.cors import ACCESS_CONTROL_ALLOW_ORIGIN, DEFAULT_ALLOW_HEADERS

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)
web_app = web.Application(middlewares=[
    cors_middleware(allow_all=True, allow_headers=DEFAULT_ALLOW_HEADERS + (ACCESS_CONTROL_ALLOW_ORIGIN,))
])
routes = web.RouteTableDef()
loop = asyncio.get_event_loop()
lock = asyncio.Lock()
pyro_app: Optional[Client] = None
server: Optional[web.AppRunner] = None
CONFIG_FILE_URL = os.getenv("CONFIG_FILE_URL")
USER_SESSION_STRING = None
TG_API_ID = None
TG_API_HASH = None
TARGET_CHAT_ID: Optional[str] = None
SERVER_PORT = int(os.environ.get('SERVER_PORT', 8080))


async def setup_config():
    global USER_SESSION_STRING
    global TG_API_ID
    global TG_API_HASH
    global TARGET_CHAT_ID
    is_config_ok = False
    if CONFIG_FILE_URL is not None:
        logger.info("Downloading config file")
        try:
            async with ClientSession(timeout=ClientTimeout(total=10)) as session:
                async with session.get(url=CONFIG_FILE_URL) as response:
                    if response.ok:
                        with open('config.env', 'wt', encoding='utf-8') as f:
                            f.write(await response.text(encoding='utf-8'))
                        logger.info("Loading config values")
                        if load_dotenv('config.env', override=True):
                            TG_API_HASH = os.getenv("TG_API_HASH")
                            TG_API_ID = os.getenv("TG_API_ID")
                            TARGET_CHAT_ID = os.getenv("TARGET_CHAT_ID")
                            USER_SESSION_STRING = os.getenv("USER_SESSION_STRING")
                            if all([TG_API_HASH, TG_API_ID, TARGET_CHAT_ID, USER_SESSION_STRING]):
                                is_config_ok = True
                    else:
                        logger.error("Error while downloading config file")
        except TimeoutError:
            logger.error("Failed to download config file")
    else:
        logger.error("CONFIG_FILE_URL is None")
    if is_config_ok is False:
        logger.error("Config is not set properly..exiting")
        exit(os.EX_CONFIG)


async def start_pyrogram() -> bool:
    global pyro_app
    logger.info("Starting pyrogram session")
    try:
        async with lock:
            pyro_app = Client(
                name="requestForwarder",
                api_id=TG_API_ID,
                api_hash=TG_API_HASH,
                session_string=USER_SESSION_STRING,
                no_updates=True,
                parse_mode=enums.ParseMode.HTML,
                in_memory=True,
                takeout=True,
                max_concurrent_transmissions=5)
            await pyro_app.start()
            logger.info(f"Session started, username: {pyro_app.me.username}")
            return True
    except ConnectionError:
        logger.warning("Pyrogram session already started")
    except errors.RPCError as e:
        logger.error(f"Failed to start pyrogram session, error: {e.MESSAGE}")
    return False


async def start_web_server():
    global server
    logger.info("Setting up web server")
    web_app.add_routes(routes)
    server = web.AppRunner(web_app)
    await server.setup()
    await web.TCPSite(runner=server, host='0.0.0.0', port=SERVER_PORT).start()
    logger.info(f"Web server started on port:: {SERVER_PORT}")


async def start_services():
    await setup_config()
    await start_pyrogram()
    await start_web_server()
    await idle()


async def restart_bot() -> bool:
    if pyro_app:
        logger.info("Stopping bot session")
        try:
            async with lock:
                await pyro_app.stop(block=True)
        except ConnectionError:
            logger.warning("Bot is already stopped")
        except errors.TakeoutInvalid as e:
            logger.error(e.MESSAGE)
        return await start_pyrogram()
    return False


@retry(wait=wait_exponential(multiplier=2, min=3, max=6), stop=stop_after_attempt(3),
       retry=(retry_if_exception_type(errors.FloodWait)))
async def send_message(msg: str, response: dict):
    try:
        await pyro_app.send_message(chat_id=TARGET_CHAT_ID, text=msg)
    except errors.FloodWait as f:
        logger.warning(f"Retrying sending message [{f.MESSAGE}]")
        raise f
    except errors.TakeoutInvalid as e:
        logger.error(f"{e.MESSAGE}...Restarting bot session")
        if await restart_bot() is True:
            raise errors.FloodWait
        else:
            return web.json_response(data=response, status=HTTPStatus.INTERNAL_SERVER_ERROR.value)
    except errors.RPCError as e:
        err_msg = f"Failed to send message [{e.MESSAGE}]"
        logger.error(err_msg)
        response["error"] = err_msg
        return web.json_response(data=response, status=HTTPStatus.INTERNAL_SERVER_ERROR.value)
    else:
        response["status"] = "File is requested"
        logger.info(f"[REQUESTED] File: {response.get('fileName')}")
        return web.json_response(data=response, status=HTTPStatus.OK.value)


@routes.get("/")
async def root_route(request: web.Request):
    return web.json_response({
        "msg": "Hello from TG message service"
    })


@routes.get("/get/{file_name}/{file_id}")
async def request_file(request: web.Request):
    file_name = request.match_info['file_name']
    file_id = request.match_info['file_id']
    logger.info(f"Received request to fetch file: {file_name} id: {file_id}")
    response = {"fileName": file_name, "fileId": file_id}
    if not pyro_app.me:
        err_msg = "Pyrogram session is not initialized"
        logger.error(err_msg)
        response["error"] = err_msg
        return web.json_response(data=response, status=HTTPStatus.INTERNAL_SERVER_ERROR.value)
    get_cmd_txt = f"/get {file_id}"
    try:
        return await send_message(get_cmd_txt, response)
    except RetryError as e:
        err_msg = f"Unable to send message even after retrying for {e.last_attempt.attempt_number} attempts"
        response["error"] = err_msg
        logger.error(err_msg)
        return web.json_response(data=response, status=HTTPStatus.INTERNAL_SERVER_ERROR.value)


@routes.get("/status")
async def health_check(request: web.Request):
    try:
        if (not all([TG_API_ID, TG_API_HASH, TARGET_CHAT_ID, USER_SESSION_STRING, pyro_app.me]) or
                not pyro_app.me.username):
            return web.json_response(data={"status": "missing required config"},
                                     status=HTTPStatus.INTERNAL_SERVER_ERROR.value)
        else:
            return web.json_response(data={
                "status": "ok",
                "userName": pyro_app.me.username,
                "botStatus": pyro_app.me.status.name,
                "device": pyro_app.device_model,
                "version": pyro_app.system_version}, status=HTTPStatus.OK.value)
    except errors.RPCError as e:
        return web.json_response(data={"status": "pyrogram session not initialized", "error": e.MESSAGE},
                                 status=HTTPStatus.INTERNAL_SERVER_ERROR.value)


async def cleanup():
    if all([pyro_app, server]):
        await server.cleanup()
        await pyro_app.stop()
        logger.info("Web server and bot stopped")
    else:
        logger.warning("Unable to run cleanup process")


if __name__ == "__main__":
    try:
        loop.run_until_complete(start_services())
    except KeyboardInterrupt:
        pass
    except Exception as err:
        logger.error(err.with_traceback(None))
    finally:
        loop.run_until_complete(cleanup())
        loop.stop()
        logger.info("Stopped Services")
