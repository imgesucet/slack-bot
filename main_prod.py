import json
import logging
import os
import re
import threading

from slack_sdk.web import WebClient
from slack_sdk.http_retry.builtin_handlers import RateLimitErrorRetryHandler
from slack_bolt import App, Ack, BoltContext
from flask import Flask, jsonify, request

from app.bolt_listeners import register_listeners, before_authorize
from app.env import (
    SLACK_APP_LOG_LEVEL,
)
from app.slack_ops import (
    build_home_tab,
    DEFAULT_HOME_TAB_MESSAGE,
    DEFAULT_HOME_TAB_CONFIGURE_LABEL
)

import boto3

from main_handlers import handle_use_db_func, handle_set_key_func, handle_suggest_func, handle_preview_func, \
    handle_get_db_urls_func, handle_set_db_url_func, handle_get_db_tables_func, handle_set_db_table_func, \
    set_s3_openai_api_key_func, handle_help_actions_func, handle_set_chat_history_size_func, handle_predict_func, \
    render_home_tab_func
from main_prod_funcs import validate_api_key_registration, save_api_key_registration
from slack_handler import SlackRequestHandler
from slack_s3_oauth_flow import LambdaS3OAuthFlow
from slack_bolt.oauth.oauth_settings import OAuthSettings

logging.basicConfig(format="%(asctime)s %(message)s", level=SLACK_APP_LOG_LEVEL)

AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")
AWS_STORAGE_BUCKET_NAME = os.environ.get("AWS_STORAGE_BUCKET_NAME")
AWS_S3_REGION_NAME = os.environ.get("AWS_S3_REGION_NAME", "nl-ams")
AWS_S3_ENDPOINT_URL = os.environ.get("AWS_S3_ENDPOINT_URL")
AWS_S3_FILE_OVERWRITE = os.environ.get("AWS_S3_FILE_OVERWRITE", False)

SLACK_CLIENT_ID = os.environ.get("SLACK_CLIENT_ID")
SLACK_CLIENT_SECRET = os.environ.get("SLACK_CLIENT_SECRET")

GPTINSLACK_HOST = os.environ.get("GPTINSLACK_HOST")
PREFIX = ""
if GPTINSLACK_HOST == "https://gptinslack.defytrends.dev":
    PREFIX = "p"

s3_client = boto3.client(
    's3',
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    endpoint_url=AWS_S3_ENDPOINT_URL,
    region_name=AWS_S3_REGION_NAME,
    verify=True  # Consider this only if you have SSL issues, but be aware of the security implications
)

client_template = WebClient()
client_template.retry_handlers.append(RateLimitErrorRetryHandler(max_retry_count=2))


def register_revocation_handlers(app: App):
    # Handle uninstall events and token revocations
    @app.event("tokens_revoked")
    def handle_tokens_revoked_events(
            event: dict,
            context: BoltContext,
            logger: logging.Logger,
    ):
        logger.info("register_revocation_handlers, init")
        user_ids = event.get("tokens", {}).get("oauth", [])
        if len(user_ids) > 0:
            for user_id in user_ids:
                try:
                    app.installation_store.delete_installation(
                        enterprise_id=context.enterprise_id,
                        team_id=context.team_id,
                        user_id=user_id,
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to installation_store.delete_installation: (team_id: {context.team_id}, enterprise_id:{context.enterprise_id}, user_id={user_id}, error: {e})"
                    )
        bots = event.get("tokens", {}).get("bot", [])
        if len(bots) > 0:
            try:
                app.installation_store.delete_bot(
                    enterprise_id=context.enterprise_id,
                    team_id=context.team_id,
                )
            except Exception as e:
                logger.error(
                    f"Failed to delete_bot: (team_id: {context.team_id}, enterprise_id:{context.enterprise_id}, error: {e})"
                )
            try:
                s3_client.delete_object(Bucket=AWS_STORAGE_BUCKET_NAME, Key=context.team_id)
            except Exception as e:
                logger.error(
                    f"Failed to delete an OpenAI auth key: (team_id: {context.team_id}, error: {e})"
                )

    @app.event("app_uninstalled")
    def handle_app_uninstalled_events(
            context: BoltContext,
            logger: logging.Logger,
    ):
        logger.info("handle_app_uninstalled_events, init")
        try:
            app.installation_store.delete_all(
                enterprise_id=context.enterprise_id,
                team_id=context.team_id,
            )
        except Exception as e:
            logger.error(
                f"Failed to delete_bot: (team_id: {context.team_id}, enterprise_id:{context.enterprise_id}, error: {e})"
            )
        try:
            s3_client.delete_object(Bucket=AWS_STORAGE_BUCKET_NAME, Key=context.team_id)
        except Exception as e:
            logger.error(
                f"Failed to delete an OpenAI auth key: (team_id: {context.team_id}, error: {e})"
            )


oauth_settings = OAuthSettings(
    client_id=SLACK_CLIENT_ID,
    client_secret=SLACK_CLIENT_SECRET,
    redirect_uri=f"{GPTINSLACK_HOST}/slack/oauth_redirect",  # Optional
    install_page_rendering_enabled=True,
    # scopes=["channels:read", "groups:read", ...],  # Add the scopes your app needs
    # redirect_uri="YOUR_OAUTH_REDIRECT_URL",  # This should match the Redirect URL set in your Slack app settings
    install_path="/slack/install",  # The endpoint users visit to install the app
    redirect_uri_path="/slack/oauth_redirect",  # The endpoint Slack redirects to after the user authorizes your app
    # state_store=...,  # This could be FileOAuthStateStore or some custom state store you create
    # installation_store=...,  # This could be FileInstallationStore or some custom installation store you create
)
app = App(
    process_before_response=True,
    before_authorize=before_authorize,
    oauth_flow=LambdaS3OAuthFlow(settings=oauth_settings),
    client=client_template,
)
register_listeners(app)
register_revocation_handlers(app)


@app.middleware
def log_request(logger, body, next):
    logger.debug(body)
    return next()


@app.middleware
def set_s3_openai_api_key(context: BoltContext, next_, logger: logging.Logger):
    return set_s3_openai_api_key_func(context, next_, logger, s3_client, AWS_STORAGE_BUCKET_NAME)


@app.command(f"/{PREFIX}set_db_table")
def handle_set_db_table(ack, command, respond, context: BoltContext, logger: logging.Logger, client: WebClient,
                        payload: dict):
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
                     args=(ack, command, respond, context, logger, client, s3_client, AWS_STORAGE_BUCKET_NAME)).start()


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


@app.command(f"/{PREFIX}set_key")
def handle_set_key(ack, command, respond, context: BoltContext, logger: logging.Logger, client):
    threading.Thread(target=handle_set_key_func,
                     args=(ack, command, respond, context, logger, client, s3_client, AWS_STORAGE_BUCKET_NAME)).start()


@app.command(f"/{PREFIX}use_db")
def handle_use_db(ack, command, respond, context: BoltContext, logger: logging.Logger, client):
    threading.Thread(target=handle_use_db_func,
                     args=(ack, command, respond, context, logger, client, s3_client, AWS_STORAGE_BUCKET_NAME)).start()


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
    return handle_help_actions_func(ack, body, say)


@app.event("app_home_opened")
def render_home_tab(client: WebClient, context: BoltContext, logger: logging.Logger):
    render_home_tab_func(client, context, logger, s3_client, AWS_STORAGE_BUCKET_NAME)


@app.action("configure")
def handle_some_action(ack, body: dict, client: WebClient, context: BoltContext, logger: logging.Logger):
    logger.info("handle_some_action, init")
    ack()
    api_key_text = "Save your Genie API key:"
    submit = "Submit"
    cancel = "Cancel"

    client.views_open(
        trigger_id=body["trigger_id"],
        view={
            "type": "modal",
            "callback_id": "configure",
            "title": {"type": "plain_text", "text": "Genie API Key"},
            "submit": {"type": "plain_text", "text": submit},
            "close": {"type": "plain_text", "text": cancel},
            "blocks": [
                {
                    "type": "input",
                    "block_id": "api_key",
                    "label": {"type": "plain_text", "text": api_key_text},
                    "element": {"type": "plain_text_input", "action_id": "input"},
                },
            ],
        },
    )


@app.view("configure")
def handle_modal_submission(ack, view: dict, context: BoltContext, logger):
    ack()
    logger.info("handle_modal_submission, configure, init")

    try:
        validate_api_key_registration(view, context, logger)
    except Exception as e:
        logger.exception(e)
        ack(
            response_action="errors",
            errors={"model": "failed to save api key"},
        )
        return

    ack()
    try:
        save_api_key_registration(view, logger, context, s3_client, AWS_STORAGE_BUCKET_NAME)
    except Exception as e:
        logger.exception(e)
        ack(
            response_action="errors",
            errors={"model": "failed to save api key"},
        )
        return

    return


slack_handler = SlackRequestHandler(app=app)

flask_app = Flask(__name__)
logger = logging.getLogger()
logger.setLevel(logging.INFO)


@flask_app.route("/slack/configure", methods=["POST"])
def slack_configure():
    logger.info("slack_configure, init")

    payload = request.json
    view = payload.get('view', {})
    context = BoltContext()  # Again, assuming fictional context creation.

    try:
        validate_api_key_registration(view, context, logger)
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

    try:
        save_api_key_registration(view, logger, context, s3_client, AWS_STORAGE_BUCKET_NAME)
    except Exception as e:
        logger.exception(e)
        return jsonify({'status': 'error', 'message': str(e)})

    return jsonify({'status': 'ok'})


@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    return slack_handler.handle(req=request)


@flask_app.route("/healthcheck", methods=['GET'])
def health_check():
    return jsonify({"status": "ok"}), 200


@flask_app.route("/slack/oauth_redirect", methods=["GET"])
def oauth_redirect():
    return slack_handler.handle(req=request)


@flask_app.route("/slack/install", methods=["GET"])
def install():
    return slack_handler.handle(request)


@flask_app.route('/slack/interactions', methods=['POST'])
def handle_interaction():
    return slack_handler.handle(request)
