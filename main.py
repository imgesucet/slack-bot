import logging
import os
from flask import Flask, jsonify
import threading

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
    OPENAI_DEPLOYMENT_ID,
)
from app.slack_ops import (
    build_home_tab,
    DEFAULT_HOME_TAB_MESSAGE,
    DEFAULT_HOME_TAB_CONFIGURE_LABEL,
)
from app.i18n import translate


if __name__ == "__main__":

    # Create a Flask application
    healthcheck_app = Flask(__name__)


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
    def set_openai_api_key(context: BoltContext, next_):
        context["OPENAI_API_KEY"] = os.environ["OPENAI_API_KEY"]
        context["OPENAI_MODEL"] = OPENAI_MODEL
        context["OPENAI_TEMPERATURE"] = OPENAI_TEMPERATURE
        context["OPENAI_API_TYPE"] = OPENAI_API_TYPE
        context["OPENAI_API_BASE"] = OPENAI_API_BASE
        context["OPENAI_API_VERSION"] = OPENAI_API_VERSION
        context["OPENAI_DEPLOYMENT_ID"] = OPENAI_DEPLOYMENT_ID
        next_()



    @app.command("/set_db_table")
    def handle_configure_command_set_db_table(ack, body, command, respond):
        # Acknowledge command request
        ack()

        value = command['text']
        print(f"set_db_table!!!, value={value}")

        if value:
            respond(text=f"DB Table set to: {value}")  # Respond to the command
        else:
            respond(text="You must provide the DB Table after /set_db_table")
    @app.command("/set_db_url")
    def handle_configure_command_set_db_url(ack, body, command, respond):
        # Acknowledge command request
        ack()

        value = command['text']
        print(f"set_db_url!!!, value={value}")

        if value:
            respond(text=f"DB URL set to: {value}")  # Respond to the command
        else:
            respond(text="You must provide the DB URL after /set_db_url")

    @app.command("/set_db_type")
    def handle_configure_comman_set_db_type(ack, body, command, respond):
        # Acknowledge command request
        ack()

        value = command['text']
        print(f"set_db_type!!!, value={value}")

        if value:
            respond(text=f"DB type set to: {value}")  # Respond to the command
        else:
            respond(text="You must provide the DB Type after /set_db_type")

    @app.command("/set_key")
    def handle_configure_command_set_key(ack, body, command, respond):
        # Acknowledge command request
        ack()

        api_key = command['text']
        print(f"set_key!!!, api_key={api_key}")

        if api_key:
            respond(text=f"API Key set to: {api_key}")  # Respond to the command
        else:
            respond(text="You must provide an API key after /set_key")


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

    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    handler.start()
