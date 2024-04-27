from pyrogram import Client, errors, enums
from flask import Flask, jsonify
from flask_cors import CORS
from http import HTTPStatus
from typing import Optional
from sys import exit as _exit
from dotenv import load_dotenv
from json import JSONDecodeError
from time import sleep
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
        except (requests.exceptions.HTTPError, requests.exceptions.ConnectionError, KeyError, JSONDecodeError):
            logger.error("Failed to setup config")
            _exit(os.EX_CONFIG)
    else:
        logger.error("CONFIG_FILE_URL is not present")
        _exit(os.EX_UNAVAILABLE)


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
        _exit(os.EX_NOTFOUND)
    except errors.RPCError as err:
        logger.error(f"Failed to start pyrogram session, error: {err.MESSAGE}")
        _exit(os.EX_UNAVAILABLE)


def send_message(msg: str, response: dict):
    try:
        pyro_app.send_message(chat_id=TARGET_CHAT_ID, text=msg)
    except errors.FloodWait as f:
        logger.warning(f"Retrying sending message [{f.MESSAGE}]")
        sleep(f.value * 1.2)
        return send_message(msg, response)
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
start_pyrogram()


@flask_app.get("/get/<file_name>/<file_id>")
def request_file(file_name: str, file_id: str):
    logger.info(f"Received request to fetch file: {file_name} id: {file_id}")
    response = {"fileName": file_name, "fileId": file_id}
    if not pyro_app:
        err_msg = "Pyrogram session is not initialized"
        logger.error(err_msg)
        response["error"] = err_msg
        return jsonify(response), HTTPStatus.INTERNAL_SERVER_ERROR
    get_cmd_txt = f"/get {file_id}"
    return send_message(get_cmd_txt, response)

