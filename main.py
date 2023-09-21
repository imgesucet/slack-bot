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
from app.slack_ops import (
    build_home_tab,
    DEFAULT_HOME_TAB_MESSAGE,
    DEFAULT_HOME_TAB_CONFIGURE_LABEL
)
from main_handlers import handle_use_db_func, handle_set_key_func, handle_suggest_func, handle_preview_func, \
    handle_get_db_urls_func, handle_set_db_url_func, handle_get_db_tables_func, handle_set_db_table_func, \
    set_s3_openai_api_key_func, handle_help_actions_func

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
        logger.info("render_home_tab, init")

        message = DEFAULT_HOME_TAB_MESSAGE
        configure_label = DEFAULT_HOME_TAB_CONFIGURE_LABEL
        try:
            response = s3_client.get_object(Bucket=AWS_STORAGE_BUCKET_NAME, Key=context.team_id)
            body = response['Body'].read().decode('utf-8')
            data = json.loads(body)
            if data["api_key"] is not None:
                message = "This app is ready to use in this workspace :raised_hands:"
            else:
                message = "This app is NOT ready to use in this workspace. Please configure it."
        except:  # noqa: E722
            pass

        client.views_publish(
            user_id=context.user_id,
            view=build_home_tab(message, configure_label),
        )


    @app.middleware
    def set_s3_openai_api_key(context: BoltContext, next_, logger: logging.Logger):
        return set_s3_openai_api_key_func(context, next_, logger, s3_client, AWS_STORAGE_BUCKET_NAME)


    @app.command("/dset_db_table")
    def handle_set_db_table(ack, command, respond, context: BoltContext, logger: logging.Logger,
                            client: WebClient, payload: dict):
        return handle_set_db_table_func(ack, command, respond, context, logger, client, payload, s3_client,
                                        AWS_STORAGE_BUCKET_NAME)


    @app.command("/dget_db_tables")
    def handle_get_db_tables(ack, command, respond, context: BoltContext, logger: logging.Logger, client: WebClient,
                             payload: dict):
        return handle_get_db_tables_func(ack, command, respond, context, logger, client, payload)


    @app.command("/dset_db_url")
    def handle_set_db_url(ack, command, respond, context: BoltContext, logger: logging.Logger, client):
        return handle_set_db_url_func(ack, command, respond, context, logger, client, s3_client,
                                      AWS_STORAGE_BUCKET_NAME)


    @app.command("/dget_db_urls")
    def handle_get_db_urls(ack, respond, context: BoltContext, logger: logging.Logger, client):
        return handle_get_db_urls_func(ack, respond, context, logger, client)


    @app.command("/dpreview")
    def handle_preview(ack, command, respond, context: BoltContext, logger: logging.Logger, client, payload):
        return handle_preview_func(ack, command, respond, context, logger, client, payload)


    @app.command("/dsuggest")
    def handle_suggest(ack, command, respond, context: BoltContext, logger: logging.Logger, client, payload):
        return handle_suggest_func(ack, command, respond, context, logger, client, payload)


    @app.command("/dset_key")
    def handle_set_key(ack, command, respond, context: BoltContext, logger: logging.Logger, client):
        return handle_set_key_func(ack, command, respond, context, logger, client, s3_client, AWS_STORAGE_BUCKET_NAME)


    @app.command("/duse_db")
    def handle_use_db(ack, command, respond, context: BoltContext, logger: logging.Logger, client):
        return handle_use_db_func(ack, command, respond, context, logger, client, s3_client, AWS_STORAGE_BUCKET_NAME)


    @app.action(re.compile("^help:"))
    def handle_help_actions(ack, body, say):
        return handle_help_actions_func(ack, body, say)


    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()
