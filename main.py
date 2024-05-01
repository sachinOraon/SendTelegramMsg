from pyrogram import Client, errors, enums
from flask import Flask, jsonify
from flask_cors import CORS
from http import HTTPStatus
from typing import Optional
from dotenv import load_dotenv
from json import JSONDecodeError
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type, RetryError
import requests
import logging
import os

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)
pyro_app: Optional[Client] = None
flask_app = Flask(__name__)
CORS(flask_app)
CONFIG_FILE_URL = os.getenv("CONFIG_FILE_URL")
USER_SESSION_STRING = None
TG_API_ID = None
TG_API_HASH = None
TARGET_CHAT_ID: Optional[str] = None


def setup_config() -> None:
    global USER_SESSION_STRING
    global TG_API_ID
    global TG_API_HASH
    global TARGET_CHAT_ID
    if CONFIG_FILE_URL is not None:
        logger.info("Downloading config file")
        try:
            config_file = requests.get(url=CONFIG_FILE_URL, timeout=5)
            if config_file.ok:
                with open('config.env', 'wt', encoding='utf-8') as f:
                    f.write(config_file.text)
                logger.info("Loading config values")
                if load_dotenv('config.env', override=True):
                    TG_API_HASH = os.getenv("TG_API_HASH")
                    TG_API_ID = os.getenv("TG_API_ID")
                    TARGET_CHAT_ID = os.getenv("TARGET_CHAT_ID")
                    USER_SESSION_STRING = os.getenv("USER_SESSION_STRING")
            if not all([TG_API_HASH, TG_API_ID, TARGET_CHAT_ID, USER_SESSION_STRING]):
                logger.error("Failed to load config values")
                raise KeyError
            start_pyrogram()
        except (requests.exceptions.HTTPError, requests.exceptions.ConnectionError, KeyError, JSONDecodeError):
            logger.error("Failed to setup config")
    else:
        logger.error("CONFIG_FILE_URL is not present")


def start_pyrogram() -> None:
    global pyro_app
    logger.info("Starting pyrogram session")
    try:
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
        pyro_app.start()
        logger.info(f"Session started, username: {pyro_app.me.username}")
    except ConnectionError:
        logger.warning("Pyrogram session already started")
    except errors.RPCError as err:
        logger.error(f"Failed to start pyrogram session, error: {err.MESSAGE}")


@retry(wait=wait_exponential(multiplier=2, min=3, max=6), stop=stop_after_attempt(3),
       retry=(retry_if_exception_type(errors.FloodWait)))
def send_message(msg: str, response: dict):
    try:
        pyro_app.send_message(chat_id=TARGET_CHAT_ID, text=msg)
    except errors.FloodWait as f:
        logger.warning(f"Retrying sending message [{f.MESSAGE}]")
        raise f
    except errors.RPCError as e:
        err_msg = f"Failed to send message [{e.MESSAGE}]"
        logger.error(err_msg)
        response["error"] = err_msg
        return jsonify(response), HTTPStatus.INTERNAL_SERVER_ERROR
    else:
        response["status"] = "File is requested"
        logger.info(f"[REQUESTED] File: {response.get('fileName')}")
        return jsonify(response), HTTPStatus.OK


setup_config()


@flask_app.get("/get/<file_name>/<file_id>")
def request_file(file_name: str, file_id: str):
    logger.info(f"Received request to fetch file: {file_name} id: {file_id}")
    response = {"fileName": file_name, "fileId": file_id}
    if not pyro_app.me:
        err_msg = "Pyrogram session is not initialized"
        logger.error(err_msg)
        response["error"] = err_msg
        return jsonify(response), HTTPStatus.INTERNAL_SERVER_ERROR
    get_cmd_txt = f"/get {file_id}"
    try:
        return send_message(get_cmd_txt, response)
    except RetryError as err:
        err_msg = f"Unable to send message even after retrying for {err.last_attempt.attempt_number} attempts"
        response["error"] = err_msg
        logger.error(err_msg)
        return jsonify(response), HTTPStatus.INTERNAL_SERVER_ERROR


@flask_app.get("/status")
def health_check():
    try:
        if (not all([TG_API_ID, TG_API_HASH, TARGET_CHAT_ID, USER_SESSION_STRING, pyro_app.me]) or
                not pyro_app.me.username):
            return jsonify({"status": "missing required config"}), HTTPStatus.INTERNAL_SERVER_ERROR
        else:
            return (jsonify({"status": "ok", "userName": pyro_app.me.username, "botStatus": pyro_app.me.status.name,
                             "device": pyro_app.device_model, "version": pyro_app.system_version}), HTTPStatus.OK)
    except errors.RPCError as e:
        return (jsonify({"status": "pyrogram session not initialized", "error": e.MESSAGE}),
                HTTPStatus.INTERNAL_SERVER_ERROR)
