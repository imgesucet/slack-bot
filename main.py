import json
import logging
import os
import re

from flask import Flask, jsonify
import threading
import boto3

from slack_bolt import App, BoltContext
from slack_sdk.web import WebClient

from app.bolt_listeners import before_authorize, register_listeners
from app.env import (
    SLACK_APP_LOG_LEVEL,
)

from main_handlers import handle_use_db_func, handle_suggest_func, handle_preview_func, \
    handle_get_db_urls_func, handle_set_db_url_func, handle_get_db_tables_func, handle_set_db_table_func, \
    set_s3_openai_api_key_func, handle_help_actions_func, handle_set_chat_history_size_func, handle_predict_func, \
    render_home_tab_func, handle_login_func, handle_set_key_func

if __name__ == "__main__":
    # Create a Flask application
    healthcheck_app = Flask(__name__)
    AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")
    AWS_STORAGE_BUCKET_NAME = os.environ.get("AWS_STORAGE_BUCKET_NAME")
    AWS_S3_REGION_NAME = os.environ.get("AWS_S3_REGION_NAME", "nl-ams")
    AWS_S3_ENDPOINT_URL = os.environ.get("AWS_S3_ENDPOINT_URL")
    AWS_S3_FILE_OVERWRITE = os.environ.get("AWS_S3_FILE_OVERWRITE", False)
    SLACK_APP_TOKEN = os.environ["SLACK_APP_TOKEN"]
    SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
    PREFIX = "d"

    s3_client = boto3.client(
        's3',
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        endpoint_url=AWS_S3_ENDPOINT_URL,
        region_name=AWS_S3_REGION_NAME,
        verify=True  # Consider this only if you have SSL issues, but be aware of the security implications
    )


    # Define a simple healthcheck endpoint
    @healthcheck_app.route("/healthcheck", methods=['GET'])
    def health_check():
        return jsonify({"status": "ok"}), 200


    # Create a function that starts the Flask server
    def start_healthcheck_server():
        port = int(os.getenv('PORT', 9891))
        healthcheck_app.run(host='0.0.0.0', port=port)


    # Wrap your Flask server start inside a thread, so it doesn't block your Slack bot
    healthcheck_thread = threading.Thread(target=start_healthcheck_server)
    healthcheck_thread.start()

    from slack_bolt.adapter.socket_mode import SocketModeHandler

    logging.basicConfig(level=SLACK_APP_LOG_LEVEL)

    app = App(
        token=SLACK_BOT_TOKEN,
        before_authorize=before_authorize,
        process_before_response=True,
    )
    # app.client.retry_handlers.append(RateLimitErrorRetryHandler(max_retry_count=2))

    register_listeners(app)


    @app.event("app_home_opened")
    def render_home_tab(client: WebClient, context: BoltContext, logger: logging.Logger):
        render_home_tab_func(client, context, logger, s3_client, AWS_STORAGE_BUCKET_NAME)


    @app.middleware
    def set_s3_openai_api_key(context: BoltContext, next_, logger: logging.Logger):
        return set_s3_openai_api_key_func(context, next_, logger, s3_client, AWS_STORAGE_BUCKET_NAME)


    @app.command(f"/{PREFIX}set_db_table")
    def handle_set_db_table(ack, command, respond, context: BoltContext, logger: logging.Logger,
                            client: WebClient, payload: dict):
        threading.Thread(target=handle_set_db_table_func,
                         args=(ack, command, respond, context, logger, client, payload, s3_client,
                               AWS_STORAGE_BUCKET_NAME)).start()


    @app.command(f"/{PREFIX}get_db_tables")
    def handle_get_db_tables(ack, command, respond, context: BoltContext, logger: logging.Logger, client: WebClient,
                             payload: dict):
        threading.Thread(target=handle_get_db_tables_func,
                         args=(ack, command, respond, context, logger, client, payload)).start()


    @app.command(f"/{PREFIX}set_db_url")
    def handle_set_db_url(ack, command, respond, context: BoltContext, logger: logging.Logger, client):
        threading.Thread(target=handle_set_db_url_func,
                         args=(ack, command, respond, context, logger, client, s3_client,
                               AWS_STORAGE_BUCKET_NAME)).start()


    @app.command(f"/{PREFIX}get_db_urls")
    def handle_get_db_urls(ack, respond, context: BoltContext, logger: logging.Logger, client):
        threading.Thread(target=handle_get_db_urls_func,
                         args=(ack, respond, context, logger, client)).start()


    @app.command(f"/{PREFIX}preview")
    def handle_preview(ack, command, respond, context: BoltContext, logger: logging.Logger, client, payload):
        threading.Thread(target=handle_preview_func,
                         args=(ack, command, respond, context, logger, client, payload)).start()


    @app.command(f"/{PREFIX}suggest")
    def handle_suggest(ack, command, respond, context: BoltContext, logger: logging.Logger, client, payload):
        threading.Thread(target=handle_suggest_func,
                         args=(ack, command, respond, context, logger, client, payload)).start()


    @app.command(f"/{PREFIX}login")
    def handle_login(ack, command, respond, context: BoltContext, logger: logging.Logger, client):
        threading.Thread(target=handle_login_func,
                         args=(ack, command, respond, context, logger, client, s3_client,
                               AWS_STORAGE_BUCKET_NAME)).start()

    @app.command(f"/{PREFIX}set_key")
    def handle_set_key(ack, command, respond, context: BoltContext, logger: logging.Logger, client):
        threading.Thread(target=handle_set_key_func,
                         args=(ack, command, respond, context, logger, client, s3_client,
                               AWS_STORAGE_BUCKET_NAME)).start()


    @app.command(f"/{PREFIX}use_db")
    def handle_use_db(ack, command, respond, context: BoltContext, logger: logging.Logger, client):
        threading.Thread(target=handle_use_db_func,
                         args=(ack, command, respond, context, logger, client, s3_client,
                               AWS_STORAGE_BUCKET_NAME)).start()


    @app.command(f"/{PREFIX}set_chat_history_size")
    def handle_set_chat_history_size(ack, command, respond, context: BoltContext, logger: logging.Logger, client):
        threading.Thread(target=handle_set_chat_history_size_func,
                         args=(ack, command, respond, context, logger, client, s3_client,
                               AWS_STORAGE_BUCKET_NAME)).start()


    @app.command(f"/{PREFIX}predict")
    def handle_predict(ack, command, respond, context: BoltContext, logger: logging.Logger, client, payload):
        threading.Thread(target=handle_predict_func,
                         args=(ack, command, respond, context, logger, client, payload)).start()


    @app.action(re.compile("^help:"))
    def handle_help_actions(ack, body, say):
        threading.Thread(target=handle_help_actions_func,
                         args=(ack, body, say)).start()


    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()
