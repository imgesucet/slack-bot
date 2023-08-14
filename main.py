import json
import logging
import os
from flask import Flask, jsonify
import threading
import boto3
import botocore

from slack_bolt import App, BoltContext
from slack_sdk.web import WebClient
from slack_sdk.http_retry.builtin_handlers import RateLimitErrorRetryHandler

from app.bolt_listeners import before_authorize, register_listeners
from app.env import (
    USE_SLACK_LANGUAGE,
    SLACK_APP_LOG_LEVEL,
    OPENAI_MODEL,
    OPENAI_TEMPERATURE,
    OPENAI_API_TYPE,
    OPENAI_API_BASE,
    OPENAI_API_VERSION,
    OPENAI_DEPLOYMENT_ID, DEFAULT_OPENAI_TEMPERATURE, DEFAULT_OPENAI_MODEL, DEFAULT_OPENAI_API_TYPE,
    DEFAULT_OPENAI_API_BASE, DEFAULT_OPENAI_API_VERSION, DEFAULT_OPENAI_DEPLOYMENT_ID,
)
from app.slack_ops import (
    build_home_tab,
    DEFAULT_HOME_TAB_MESSAGE,
    DEFAULT_HOME_TAB_CONFIGURE_LABEL,
)
from app.i18n import translate
from app.utils import post_data_to_genieapi, redact_string

if __name__ == "__main__":

    # Create a Flask application
    healthcheck_app = Flask(__name__)
    AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")
    AWS_STORAGE_BUCKET_NAME = os.environ.get("AWS_STORAGE_BUCKET_NAME")
    AWS_S3_REGION_NAME = os.environ.get("AWS_S3_REGION_NAME", "fr-par")
    AWS_S3_ENDPOINT_URL = os.environ.get("AWS_S3_ENDPOINT_URL")
    AWS_S3_FILE_OVERWRITE = os.environ.get("AWS_S3_FILE_OVERWRITE", False)

    s3_client = boto3.client(
        's3',
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        endpoint_url=AWS_S3_ENDPOINT_URL,
        region_name=AWS_S3_REGION_NAME,
        verify=False  # Consider this only if you have SSL issues, but be aware of the security implications
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
        token=os.environ["SLACK_BOT_TOKEN"],
        before_authorize=before_authorize,
        process_before_response=True,
    )
    app.client.retry_handlers.append(RateLimitErrorRetryHandler(max_retry_count=2))

    register_listeners(app)

    @app.event("app_home_opened")
    def render_home_tab(client: WebClient, context: BoltContext):
        already_set_api_key = os.environ["OPENAI_API_KEY"]
        text = translate(
            openai_api_key=already_set_api_key,
            context=context,
            text=DEFAULT_HOME_TAB_MESSAGE,
        )
        configure_label = translate(
            openai_api_key=already_set_api_key,
            context=context,
            text=DEFAULT_HOME_TAB_CONFIGURE_LABEL,
        )
        client.views_publish(
            user_id=context.user_id,
            view=build_home_tab(text, configure_label),
        )

    if USE_SLACK_LANGUAGE is True:

        @app.middleware
        def set_locale(
            context: BoltContext,
            client: WebClient,
            next_,
        ):
            user_id = context.actor_user_id or context.user_id
            user_info = client.users_info(user=user_id, include_locale=True)
            context["locale"] = user_info.get("user", {}).get("locale")
            next_()


    @app.middleware
    def set_s3_openai_api_key(context: BoltContext, next_):
        try:
            s3_response = s3_client.get_object(
                Bucket=AWS_STORAGE_BUCKET_NAME, Key=context.team_id
            )
            config_str: str = s3_response["Body"].read().decode("utf-8")
            if config_str.startswith("{"):
                config = json.loads(config_str)
                context["OPENAI_API_KEY"] = config.get("api_key")

                context["api_key"] = config.get("api_key")
                context["db_type"] = config.get("db_type")
                context["db_url"] = config.get("db_url")
                context["db_table"] = config.get("db_table")

                context["OPENAI_MODEL"] = config.get("model")
                context["OPENAI_TEMPERATURE"] = config.get(
                    "temperature", DEFAULT_OPENAI_TEMPERATURE
                )
            else:
                # The legacy data format
                context["OPENAI_API_KEY"] = config_str
                context["OPENAI_MODEL"] = DEFAULT_OPENAI_MODEL
                context["OPENAI_TEMPERATURE"] = DEFAULT_OPENAI_TEMPERATURE
            context["OPENAI_API_TYPE"] = DEFAULT_OPENAI_API_TYPE
            context["OPENAI_API_BASE"] = DEFAULT_OPENAI_API_BASE
            context["OPENAI_API_VERSION"] = DEFAULT_OPENAI_API_VERSION
            context["OPENAI_DEPLOYMENT_ID"] = DEFAULT_OPENAI_DEPLOYMENT_ID
        except:  # noqa: E722
            context["OPENAI_API_KEY"] = None
        next_()


    @app.command("/set_db_table")
    def handle_set_db_table(ack, body, command, respond, context: BoltContext, logger: logging.Logger,):
        # Acknowledge command request
        ack()

        value = command['text']
        print(f"set_db_table!!!, value={value}")

        if value:
            save_s3("db_table", value, logger, context)
            respond(text=f"DB Table set to: {value}")  # Respond to the command
        else:
            respond(text="You must provide the DB Table after. eg /set_db_table tvl")


    @app.command("/set_db_url")
    def handle_set_db_url(ack, body, command, respond, context: BoltContext, logger: logging.Logger,):
        # Acknowledge command request
        ack()

        value = command['text']
        print(f"set_db_url!!!, value={value}")

        if value:
            # save_s3("db_url", value, logger, context)
            api_key = context["api_key"]
            try:
                post_data_to_genieapi(api_key, "/update/user/database_connection", None, {"connection_string_url": value})
            except Exception as e:
                logger.exception(e)
                return respond(text=f"Failed to set DB URL to: {redact_string(value)}")  # Respond to the command

            respond(text=f"DB URL set to: {redact_string(value)}")  # Respond to the command
        else:
            respond(text="You must provide the DB URL after /set_db_url [postgres://{user}:{password}@{host}:{port}/{db_name}?sslmode=require]")

    @app.command("/set_db_type")
    def handle_set_db_type(ack, body, command, respond, context: BoltContext, logger: logging.Logger,):
        # Acknowledge command request
        ack()

        value = command['text']
        print(f"set_db_type!!!, value={value}")

        if value:
            save_s3("db_type", value, logger, context)
            respond(text=f"DB type set to: {value}")  # Respond to the command
        else:
            respond(text="You must provide the DB Type after /set_db_type POSTGRES")

    @app.command("/set_key")
    def handle_set_key(ack, body, command, respond, context: BoltContext, logger: logging.Logger,):
        # Acknowledge command request
        ack()

        api_key = command['text']
        print(f"set_key!!!, api_key={api_key}")

        if api_key:
            save_s3("api_key", api_key, logger, context)
            respond(text=f"API Key set to: {api_key}")  # Respond to the command
        else:
            respond(text="You must provide an API key after /set_key asd123")


    @app.action("configure")
    def handle_some_action(ack, body: dict, client: WebClient, context: BoltContext):
        print("configure!!!")
        ack()
        already_set_api_key = context.get("OPENAI_API_KEY")
        api_key_text = "Save your OpenAI API key:"
        submit = "Submit"
        cancel = "Cancel"
        if already_set_api_key is not None:
            api_key_text = translate(
                openai_api_key=already_set_api_key, context=context, text=api_key_text
            )
            submit = translate(
                openai_api_key=already_set_api_key, context=context, text=submit
            )
            cancel = translate(
                openai_api_key=already_set_api_key, context=context, text=cancel
            )

        client.views_open(
            trigger_id=body["trigger_id"],
            view={
                "type": "modal",
                "callback_id": "configure",
                "title": {"type": "plain_text", "text": "OpenAI API Key"},
                "submit": {"type": "plain_text", "text": submit},
                "close": {"type": "plain_text", "text": cancel},
                "blocks": [
                    {
                        "type": "input",
                        "block_id": "api_key",
                        "label": {"type": "plain_text", "text": api_key_text},
                        "element": {"type": "plain_text_input", "action_id": "input"},
                    },
                    {
                        "type": "input",
                        "block_id": "model",
                        "label": {"type": "plain_text", "text": "OpenAI Model"},
                        "element": {
                            "type": "static_select",
                            "action_id": "input",
                            "options": [
                                {
                                    "text": {
                                        "type": "plain_text",
                                        "text": "GPT-3.5 Turbo",
                                    },
                                    "value": "gpt-3.5-turbo",
                                },
                                {
                                    "text": {"type": "plain_text", "text": "GPT-4"},
                                    "value": "gpt-4",
                                },
                            ],
                            "initial_option": {
                                "text": {
                                    "type": "plain_text",
                                    "text": "GPT-3.5 Turbo",
                                },
                                "value": "gpt-3.5-turbo",
                            },
                        },
                    },
                ],
            },
        )

    def save_s3(
            key: str,
            value: str,
            logger: logging.Logger,
            context: BoltContext,
    ):
        user_id = context.actor_user_id or context.user_id

        try:
            # Step 1: Try to get the existing object from S3
            try:
                response = s3_client.get_object(
                    Bucket=AWS_STORAGE_BUCKET_NAME,
                    Key=context.team_id+"_"+user_id
                )
                body = response['Body'].read().decode('utf-8')
                data = json.loads(body)
            except s3_client.exceptions.NoSuchKey:
                # If the object doesn't exist, create a new one
                data = {}

            # Step 2: Update or set the key and value in the object
            data[key] = value

            # Step 3: Put the updated or new object back into S3
            s3_client.put_object(
                Bucket=AWS_STORAGE_BUCKET_NAME,
                Key=context.team_id+"_"+user_id,
                Body=json.dumps(data)
            )
        except botocore.exceptions.ClientError as e:
            # Specific exception handling for boto3's client errors
            logger.error(f"save_s3, Encountered an error with boto3: {e}")
        except Exception as e:
            logger.exception(e)


    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    handler.start()
