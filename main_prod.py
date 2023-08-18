import json
import logging
import os
import threading

import botocore

from slack_sdk.web import WebClient
from slack_sdk.http_retry.builtin_handlers import RateLimitErrorRetryHandler
from slack_bolt import App, Ack, BoltContext
from flask import Flask, jsonify, request

from app.bolt_listeners import register_listeners, before_authorize
from app.env import (
    SLACK_APP_LOG_LEVEL,
    DEFAULT_OPENAI_MODEL,
    DEFAULT_OPENAI_TEMPERATURE,
    DEFAULT_OPENAI_API_TYPE,
    DEFAULT_OPENAI_API_BASE,
    DEFAULT_OPENAI_API_VERSION,
    DEFAULT_OPENAI_DEPLOYMENT_ID,
)
from app.slack_ops import (
    build_home_tab,
    DEFAULT_HOME_TAB_MESSAGE,
    DEFAULT_HOME_TAB_CONFIGURE_LABEL,
)
from app.i18n import translate

import boto3

from app.utils import cool_name_generator, redact_string, post_data_to_genieapi, fetch_data_from_genieapi, \
    redact_credentials_from_url
from slack_handler import SlackRequestHandler
from slack_s3_oauth_flow import LambdaS3OAuthFlow
from slack_bolt.oauth.oauth_settings import OAuthSettings

logging.basicConfig(format="%(asctime)s %(message)s", level=SLACK_APP_LOG_LEVEL)

AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")
AWS_STORAGE_BUCKET_NAME = os.environ.get("AWS_STORAGE_BUCKET_NAME")
AWS_S3_REGION_NAME = os.environ.get("AWS_S3_REGION_NAME", "fr-par")
AWS_S3_ENDPOINT_URL = os.environ.get("AWS_S3_ENDPOINT_URL")
AWS_S3_FILE_OVERWRITE = os.environ.get("AWS_S3_FILE_OVERWRITE", False)

SLACK_APP_TOKEN = os.environ["SLACK_APP_TOKEN"]
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]

SLACK_CLIENT_ID = os.environ.get("SLACK_CLIENT_ID")
SLACK_CLIENT_SECRET = os.environ.get("SLACK_CLIENT_SECRET")

s3_client = boto3.client(
    's3',
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    endpoint_url=AWS_S3_ENDPOINT_URL,
    region_name=AWS_S3_REGION_NAME,
    verify=False  # Consider this only if you have SSL issues, but be aware of the security implications
)

client_template = WebClient(
    token=SLACK_BOT_TOKEN,
)
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
                app.installation_store.delete_installation(
                    enterprise_id=context.enterprise_id,
                    team_id=context.team_id,
                    user_id=user_id,
                )
        bots = event.get("tokens", {}).get("bot", [])
        if len(bots) > 0:
            app.installation_store.delete_bot(
                enterprise_id=context.enterprise_id,
                team_id=context.team_id,
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
        app.installation_store.delete_all(
            enterprise_id=context.enterprise_id,
            team_id=context.team_id,
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
    redirect_uri=None,  # Optional
    # scopes=["channels:read", "groups:read", ...],  # Add the scopes your app needs
    # redirect_uri="YOUR_OAUTH_REDIRECT_URL",  # This should match the Redirect URL set in your Slack app settings
    # install_path="/install",  # The endpoint users visit to install the app
    # redirect_uri_path="/slack/oauth_redirect",  # The endpoint Slack redirects to after the user authorizes your app
    # state_store=...,  # This could be FileOAuthStateStore or some custom state store you create
    # installation_store=...,  # This could be FileInstallationStore or some custom installation store you create
)
app = App(

    process_before_response=True,
    before_authorize=before_authorize,
    oauth_flow=LambdaS3OAuthFlow(settings=oauth_settings),
    client=client_template,
)
app.oauth_flow.settings.install_page_rendering_enabled = False
register_listeners(app)
register_revocation_handlers(app)


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
            context["OPENAI_MODEL"] = config.get("model")
            context["OPENAI_TEMPERATURE"] = config.get(
                "temperature", DEFAULT_OPENAI_TEMPERATURE
            )
        user_id = context.actor_user_id or context.user_id
        s3_response = s3_client.get_object(
            Bucket=AWS_STORAGE_BUCKET_NAME, Key=context.team_id + "_" + user_id
        )
        config_str: str = s3_response["Body"].read().decode("utf-8")
        if config_str.startswith("{"):
            config = json.loads(config_str)
            context["db_type"] = config.get("db_type")
            context["db_url"] = config.get("db_url")
            context["db_table"] = config.get("db_table")

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
def handle_set_db_table(ack, body, command, respond, context: BoltContext, logger: logging.Logger, ):
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
def handle_set_db_url(ack, body, command, respond, context: BoltContext, logger: logging.Logger, ):
    # Acknowledge command request
    ack()

    value = command['text']
    print(f"set_db_url!!!, value={value}")

    if value:
        # save_s3("db_url", value, logger, context)
        api_key = context["api_key"]
        try:
            resource_name = cool_name_generator(value)
            post_data_to_genieapi(api_key, "/update/user/database_connection", None,
                                  {"connection_string_url": value, "resourcename": resource_name})

            save_s3("db_url", resource_name, logger, context)
            respond(text=f"DB URL set to: {redact_string(value)}")  # Respond to the command

        except Exception as e:
            logger.exception(e)
            respond(text=f"Failed to set DB URL to: {redact_string(value)}")  # Respond to the command
            return
    else:
        respond(
            text="You must provide the DB URL after /set_db_url [postgres://{user}:{password}@{host}:{port}/{db_name}?sslmode=require]")


@app.command("/get_db_urls")
def handle_get_db_urls(ack, body, command, respond, context: BoltContext, logger: logging.Logger, ):
    # Acknowledge command request
    ack()

    print(f"get_db_urls!!!")

    api_key = context["api_key"]
    try:
        connections = fetch_data_from_genieapi(api_key, "/list/user/database_connection")

        # Create headers for the table
        table_header = "*Resource Name* | *Connection String URL*\n"
        strResponse = table_header
        separator = "---------------- | ----------------------\n"  # You can adjust the dashes as per the expected length
        strResponse += separator

        # Add each connection to the table
        for c in connections:
            print(f"get_db_urls, connections, c={c} ")
            strResponse += f"{c['resourcename']} | {redact_credentials_from_url(c['connection_string_url'])}\n"

        respond(text=strResponse)

    except Exception as e:
        logger.exception(e)
        return respond(text=f"Failed to get DB URLs")  # Respond to the command


@app.command("/set_db_type")
def handle_set_db_type(ack, body, command, respond, context: BoltContext, logger: logging.Logger, ):
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
def handle_set_key(ack, body, command, respond, context: BoltContext, logger: logging.Logger, ):
    # Acknowledge command request
    ack()

    api_key = command['text']
    print(f"set_key!!!, api_key={api_key}")

    if api_key:
        save_s3("api_key", api_key, logger, context)
        respond(text=f"API Key set to: {api_key}")  # Respond to the command
    else:
        respond(text="You must provide an API key after /set_key asd123")


def save_s3(
        key: str,
        value: str,
        logger: logging.Logger,
        context: BoltContext,
):
    user_id = context.actor_user_id or context.user_id
    if key == "db_table" or key == "db_url" or key == "db_type":
        bucket_key = context.team_id + "_" + user_id
    else:
        bucket_key = context.team_id

    try:
        # Step 1: Try to get the existing object from S3
        try:
            response = s3_client.get_object(
                Bucket=AWS_STORAGE_BUCKET_NAME,
                Key=bucket_key
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
            Key=context.team_id + "_" + user_id,
            Body=json.dumps(data)
        )
        return
    except botocore.exceptions.ClientError as e:
        # Specific exception handling for boto3's client errors
        logger.error(f"save_s3, Encountered an error ClientError, with boto3: {e}")
        return
    except Exception as e:
        logger.error(f"save_s3, Encountered an error Exception, with boto3: {e}")
        return


@app.event("app_home_opened")
def render_home_tab(client: WebClient, context: BoltContext, logger: logging.Logger):
    logger.info("render_home_tab, init")

    message = DEFAULT_HOME_TAB_MESSAGE
    configure_label = DEFAULT_HOME_TAB_CONFIGURE_LABEL
    try:
        s3_client.get_object(Bucket=AWS_STORAGE_BUCKET_NAME, Key=context.team_id)
        message = "This app is ready to use in this workspace :raised_hands:"
    except:  # noqa: E722
        pass

    openai_api_key = context.get("OPENAI_API_KEY")
    if openai_api_key is not None:
        message = translate(
            openai_api_key=openai_api_key, context=context, text=message
        )
        configure_label = translate(
            openai_api_key=openai_api_key,
            context=context,
            text=DEFAULT_HOME_TAB_CONFIGURE_LABEL,
        )

    client.views_publish(
        user_id=context.user_id,
        view=build_home_tab(message, configure_label),
    )


@app.action("configure")
def handle_some_action(ack, body: dict, client: WebClient, context: BoltContext, logger: logging.Logger):
    logger.info("handle_some_action, init")
    ack()
    already_set_api_key = context.get("OPENAI_API_KEY")
    api_key_text = "Save your Genie API key:"
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


def validate_api_key_registration(ack: Ack, view: dict, context: BoltContext, logger: logging.Logger):
    logger.info("validate_api_key_registration, init")
    ack()
    already_set_api_key = context.get("OPENAI_API_KEY")

    inputs = view["state"]["values"]
    api_key = inputs["api_key"]["input"]["value"]
    try:
        ## Verify if the API key is valid
        isauth = fetch_data_from_genieapi(api_key, "/isauth", None, None, None)
        if isauth["message"] != "ok":
            raise Exception("Invalid Genie API KEY")
        ack()
    except Exception:
        text = "This API key seems to be invalid"
        if already_set_api_key is not None:
            text = translate(
                openai_api_key=already_set_api_key, context=context, text=text
            )
        ack(
            response_action="errors",
            errors={"api_key": text},
        )


def save_api_key_registration(
        view: dict,
        logger: logging.Logger,
        context: BoltContext,
):
    logger.info("save_api_key_registration, init")
    inputs = view["state"]["values"]
    api_key = inputs["api_key"]["input"]["value"]
    model = inputs["model"]["input"]["selected_option"]["value"]
    try:
        save_s3("api_key", api_key, logger, context)
        save_s3("api_key_model", model, logger, context)

    except Exception as e:
        logger.exception(e)


app.view("configure")(
    ack=validate_api_key_registration,
    lazy=[save_api_key_registration],
)

slack_handler = SlackRequestHandler(app=app)
app_http = Flask(__name__)


@app_http.route("/slack/events", methods=["POST"])
def slack_events():
    return slack_handler.handle(req=request)


@app_http.route("/healthcheck", methods=['GET'])
def health_check():
    return jsonify({"status": "ok"}), 200


@app_http.route("/slack/oauth_redirect", methods=["GET"])
def oauth_redirect():
    return slack_handler.handle(req=request)

# Create a function that starts the Flask server
def start_healthcheck_server():
    port = int(os.getenv('PORT', 9891))
    app_http.run(host='0.0.0.0', port=port)


# Wrap your Flask server start inside a thread, so it doesn't block your Slack bot
healthcheck_thread = threading.Thread(target=start_healthcheck_server)
healthcheck_thread.start()

# port = int(os.getenv('PORT', 9891))
# if __name__ == "__main__":
#     app.start(port=port)
