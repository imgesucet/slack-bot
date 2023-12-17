import json
import logging
import botocore
import traceback

import boto3 as boto3
from slack_bolt import BoltContext
from app.bolt_listeners import DEFAULT_LOADING_TEXT, suggest_table, preview_table, predict_table, suggest_tables
from app.slack_ops import post_wip_message_with_attachment
from app.utils import send_help_buttons, fetch_data_from_genieapi, redact_credentials_from_url, cool_name_generator, \
    post_data_to_genieapi, redact_string

from app.env import (
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
    DEFAULT_HOME_TAB_CONFIGURE_LABEL
)


def set_s3_openai_api_key_func(context: BoltContext, next_, logger: logging.Logger, s3_client, AWS_STORAGE_BUCKET_NAME):
    logger.info("set_s3_openai_api_key init")
    try:
        key = context.team_id
        try:
            s3_response = s3_client.get_object(
                Bucket=AWS_STORAGE_BUCKET_NAME, Key=key
            )
            config_str: str = s3_response["Body"].read().decode("utf-8")
            if config_str.startswith("{"):
                config = json.loads(config_str)
                logger.info(f"set_s3_openai_api_key, team_id, config={config}")

                context["api_key"] = config.get("api_key")
                context["OPENAI_MODEL"] = config.get("model")
                context["OPENAI_TEMPERATURE"] = config.get(
                    "temperature", DEFAULT_OPENAI_TEMPERATURE
                )
        except s3_client.exceptions.NoSuchKey as e:
            traceback.print_exc()
            logger.error(f"set_s3_openai_api_key, team_id, key={key}, error={e}")

        user_id = context.actor_user_id or context.user_id

        key = context.team_id + "_" + user_id
        try:
            s3_response = s3_client.get_object(
                Bucket=AWS_STORAGE_BUCKET_NAME, Key=key
            )
            config_str: str = s3_response["Body"].read().decode("utf-8")
            if config_str.startswith("{"):
                config = json.loads(config_str)
                logger.info(f"set_s3_openai_api_key, team_id+user_id, config={config}")

                context["db_table"] = config.get("db_table")
                context["db_url"] = config.get("db_url")
                context["db_schema"] = config.get("db_schema")
                context["db_warehouse"] = config.get("db_warehouse")
                context["ai_engine"] = config.get("ai_engine")
                context["ai_model"] = config.get("ai_model")
                context["ai_temp"] = config.get("ai_temp")
                context["chat_history_size"] = config.get("chat_history_size")
                context["debug"] = config.get("debug")
                context["experimental_features"] = config.get("experimental_features")
            else:
                # The legacy data format
                context["OPENAI_MODEL"] = DEFAULT_OPENAI_MODEL
                context["OPENAI_TEMPERATURE"] = DEFAULT_OPENAI_TEMPERATURE
        except s3_client.exceptions.NoSuchKey as e:
            traceback.print_exc()
            logger.error(f"set_s3_openai_api_key, team_id+user_id, key={key}, error={e}")

        context["OPENAI_API_TYPE"] = DEFAULT_OPENAI_API_TYPE
        context["OPENAI_API_BASE"] = DEFAULT_OPENAI_API_BASE
        context["OPENAI_API_VERSION"] = DEFAULT_OPENAI_API_VERSION
        context["OPENAI_DEPLOYMENT_ID"] = DEFAULT_OPENAI_DEPLOYMENT_ID
    except:  # noqa: E722
        context["api_key"] = None
    next_()


def render_home_tab_func(client, context, logger, s3_client, AWS_STORAGE_BUCKET_NAME):
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


def handle_set_db_table_func(ack, command, respond, context: BoltContext, logger: logging.Logger, client, payload: dict,
                             s3_client, AWS_STORAGE_BUCKET_NAME):
    # Acknowledge command request
    ack()

    value = command['text']
    logger.info(f"set_db_table!!!, value={value}")
    respond(text=DEFAULT_LOADING_TEXT)

    if value is None or value == "":
        respond(text="You must provide the DB Table after. eg /set_db_table tvl")
        return send_help_buttons(context.channel_id, client, "")

    save_s3("db_table", value, logger, context, s3_client, AWS_STORAGE_BUCKET_NAME)
    respond(text=f"DB Table set to: {value}")  # Respond to the command
    try:
        preview_table(context, client, payload, value)
    except Exception as e:
        traceback.print_exc()
        logger.exception(e)
        respond(text=f"Failed to run preview for table")  # Respond to the command


def handle_get_db_tables_func(ack, command, respond, context: BoltContext, logger: logging.Logger, client,
                              payload: dict):
    # Acknowledge command request
    ack()

    logger.info(f"get_db_tables!!!")

    api_key = context.get("api_key")
    db_url = context.get("db_url")
    db_schema = context.get("db_schema")
    db_warehouse = context.get("db_warehouse")

    respond(text=DEFAULT_LOADING_TEXT + f", db_url={db_url}, db_schema={db_schema}, db_warehouse={db_warehouse}")

    value = command['text']

    if value == "":
        value = db_url

    if value is None or value == "":
        respond(
            text=f"Get DB Tables requires one argument Or a previously set DB with /use_db or /set_db_url")  # Respond to the command
        return send_help_buttons(context.channel_id, client, "")

    is_in_dm_with_bot = True
    messages = []
    user_id = context.actor_user_id or context.user_id

    try:
        loading_text = fetch_data_from_genieapi(
            api_key=api_key,
            endpoint="/list/user/database_connection/tables",
            db_schema=db_schema,
            db_warehouse=db_warehouse,
            resourcename=value
        )
        json_obj = loading_text["result"]
        blocks = []
        for c in json_obj:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"{c['table_name']}"
                },
                "accessory": {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": f"Use {c['table_name']} DB"
                    },
                    "value": c['table_name'],  # This will be passed to the action handler when clicked
                    "action_id": f"button:set_db_table:{c['table_name']}"
                }
            })
        respond(blocks=blocks)

        post_wip_message_with_attachment(
            client=client,
            channel=context.channel_id,
            thread_ts=payload.get("thread_ts") if is_in_dm_with_bot else payload["ts"],
            loading_text=loading_text,
            messages=messages,
            user=user_id,
            context=context,
        )

    except Exception as e:
        traceback.print_exc()
        logger.exception(e)
        respond(text=f"Failed to get DB tables")  # Respond to the command
        return send_help_buttons(context.channel_id, client, "")


def handle_get_db_schemas_func(ack, command, respond, context: BoltContext, logger: logging.Logger, client,
                               payload: dict):
    # Acknowledge command request
    ack()

    logger.info(f"handle_get_db_schemas_func!!!")
    respond(text=DEFAULT_LOADING_TEXT)

    api_key = context.get("api_key")
    db_url = context.get("db_url")

    value = command['text']

    if value == "":
        value = db_url

    if value is None or value == "":
        respond(
            text=f"Get DB Schemas requires one argument Or a previously set DB with /use_db or /set_db_url")  # Respond to the command
        return send_help_buttons(context.channel_id, client, "")

    is_in_dm_with_bot = True
    messages = []
    user_id = context.actor_user_id or context.user_id

    try:
        loading_text = fetch_data_from_genieapi(api_key=api_key,
                                                endpoint="/list/user/database_connection/schemas",
                                                resourcename=value)
        json_obj = loading_text["result"]
        blocks = []
        for c in json_obj:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"{c}"
                },
                "accessory": {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": f"Use {c} DB"
                    },
                    "value": c,  # This will be passed to the action handler when clicked
                    "action_id": f"button:set_db_schema:{c}"
                }
            })
        respond(blocks=blocks)

        post_wip_message_with_attachment(
            client=client,
            channel=context.channel_id,
            thread_ts=payload.get("thread_ts") if is_in_dm_with_bot else payload["ts"],
            loading_text=loading_text,
            messages=messages,
            user=user_id,
            context=context,
        )

    except Exception as e:
        traceback.print_exc()
        logger.exception(e)
        respond(text=f"Failed to get DB Schemas")  # Respond to the command
        return send_help_buttons(context.channel_id, client, "")


def handle_set_db_url_func(ack, command, respond, context: BoltContext, logger: logging.Logger, client, s3_client,
                           AWS_STORAGE_BUCKET_NAME):
    # Acknowledge command request
    ack()

    value = command['text']
    value = value.replace('```', '').replace('`', '').strip()

    logger.info(f"set_db_url!!!, value={value}")
    respond(text=DEFAULT_LOADING_TEXT)

    if value is None or value == "":
        respond(
            text="You must provide the DB URL after /set_db_url [postgres://{user}:{password}@{host}:{port}/{db_name}?sslmode=require]")
        return send_help_buttons(context.channel_id, client, "")

    api_key = context["api_key"]
    try:
        resource_name = cool_name_generator(value)
        post_data_to_genieapi(api_key, "/update/user/database_connection", None,
                              {"connection_string_url": value, "resourcename": resource_name})

        save_s3("db_url", resource_name, logger, context, s3_client, AWS_STORAGE_BUCKET_NAME)
        save_s3("db_schema", "", logger, context, s3_client, AWS_STORAGE_BUCKET_NAME)

        respond(text=f"DB URL set to: {redact_string(resource_name)}")  # Respond to the command

    except Exception as e:
        traceback.print_exc()
        logger.exception(e)
        respond(text=f"Failed to set DB URL to: {redact_string(value)}")  # Respond to the command
        return send_help_buttons(context.channel_id, client, "")


def handle_get_db_urls_func(ack, respond, context: BoltContext, logger: logging.Logger, client):
    # Acknowledge command request
    ack()

    logger.info(f"handle_get_db_urls_func!!!")
    respond(text=DEFAULT_LOADING_TEXT)

    api_key = context["api_key"]
    try:
        connections = fetch_data_from_genieapi(api_key=api_key, endpoint="/list/user/database_connection")

        # Create blocks with buttons for each connection
        blocks = []
        for c in connections:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"{c['resourcename']} | {redact_credentials_from_url(c['connection_string_url'])}"
                },
                "accessory": {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": f"Use {c['resourcename']} DB"
                    },
                    "value": c['resourcename'],  # This will be passed to the action handler when clicked
                    "action_id": f"button:use_db:{c['resourcename']}"
                }
            })

        respond(blocks=blocks)

    except Exception as e:
        traceback.print_exc()
        logger.exception(e)
        respond(text=f"Failed to get DB URLs")  # Respond to the command
        return send_help_buttons(context.channel_id, client, "")


def handle_preview_func(ack, command, respond, context: BoltContext, logger: logging.Logger, client, payload):
    # Acknowledge command request
    ack()

    db_table = context["db_table"]
    value = command['text']
    logger.info(f"preview!!!, value={value}")
    if not value:
        value = db_table
    respond(text=DEFAULT_LOADING_TEXT)

    try:
        preview_table(context, client, payload, value)
    except Exception as e:
        traceback.print_exc()
        logger.exception(e)
        respond(text=f"Failed to run preview for table")  # Respond to the command
        return send_help_buttons(context.channel_id, client, "")


def handle_suggest_func(ack, command, respond, context, logger, client, payload):
    # Acknowledge command request
    ack()

    db_table = context["db_table"]
    value = command['text']
    logger.info(f"suggest!!!, value={value}")
    if not value:
        value = db_table

    respond(text=DEFAULT_LOADING_TEXT)

    try:
        suggest_table(context, client, payload, value)
    except Exception as e:
        traceback.print_exc()
        logger.exception(e)
        respond(text=f"Failed to run suggest for table")  # Respond to the command
        return send_help_buttons(context.channel_id, client, "")


def handle_set_key_func(ack, command, respond, context: BoltContext, logger: logging.Logger, client, s3_client,
                        AWS_STORAGE_BUCKET_NAME):
    # Acknowledge command request
    ack()

    api_key = command['text']
    logger.info(f"set_key!!!, api_key={api_key}")

    if api_key is None or api_key == "":
        respond(text="You must provide an API key after /set_key asd123")
        return send_help_buttons(context.channel_id, client, "")

    save_s3("api_key", api_key, logger, context, s3_client, AWS_STORAGE_BUCKET_NAME)
    respond(text=f"API Key set to: {api_key}")  # Respond to the command


def handle_set_db_schema_func(ack, command, respond, context: BoltContext, logger: logging.Logger, client, s3_client,
                              AWS_STORAGE_BUCKET_NAME):
    # Acknowledge command request
    ack()

    value = command['text']
    logger.info(f"set_schema!!!, value={value}")

    save_s3("db_schema", value, logger, context, s3_client, AWS_STORAGE_BUCKET_NAME)
    respond(text=f"Database schema set to: {value}")  # Respond to the command


def handle_set_ai_engine_func(ack, command, respond, context: BoltContext, logger: logging.Logger, client, s3_client,
                              AWS_STORAGE_BUCKET_NAME):
    # Acknowledge command request
    ack()

    value = command['text']
    logger.info(f"set_ai_engine!!!, value={value}")

    save_s3("ai_engine", value, logger, context, s3_client, AWS_STORAGE_BUCKET_NAME)
    save_s3("db_schema", "", logger, context, s3_client, AWS_STORAGE_BUCKET_NAME)
    save_s3("db_table", "", logger, context, s3_client, AWS_STORAGE_BUCKET_NAME)

    respond(text=f"AI Engine set to: {value}")  # Respond to the command


def handle_login_func(ack, command, respond, context: BoltContext, logger: logging.Logger, client, s3_client,
                      AWS_STORAGE_BUCKET_NAME):
    # Acknowledge command request
    ack()

    api_key = context["api_key"]
    value = command['text']
    logger.info(f"handle_login_func!!!, value={value}")
    team_id = context.team_id
    user_id = context.user_id
    if value is None or value == "":
        respond(text="You must provide a valid email key after /login test@mail.com")
        return send_help_buttons(context.channel_id, client, "")

    post_data_to_genieapi(api_key, "/link_app_user_to_company", None,
                          {"email": value, "team_id_slack": team_id, "user_id_slack": user_id, "app_type": "slack"})

    save_s3("email", value, logger, context, s3_client, AWS_STORAGE_BUCKET_NAME)

    respond(
        text=f"A confirmation email has been sent to: {value}, please confirm your login by accepting it.")  # Respond to the command


def handle_use_db_func(ack, command, respond, context: BoltContext, logger: logging.Logger, client,
                       s3_client, AWS_STORAGE_BUCKET_NAME):
    # Acknowledge command request
    ack()

    value = command['text']
    logger.info(f"use_db!!!, value={value}")
    respond(text=DEFAULT_LOADING_TEXT)

    if value is None or value == "":
        respond(text="You must provide the DB alias after. eg /use_db bold-sky")
        return send_help_buttons(context.channel_id, client, "")

    save_s3("db_url", value, logger, context, s3_client, AWS_STORAGE_BUCKET_NAME)
    save_s3("db_schema", "", logger, context, s3_client, AWS_STORAGE_BUCKET_NAME)

    respond(text=f"Default DB for queries set to: {value}")  # Respond to the command


def handle_set_chat_history_size_func(ack, command, respond, context: BoltContext, logger: logging.Logger, client,
                                      s3_client,
                                      AWS_STORAGE_BUCKET_NAME):
    # Acknowledge command request
    ack()

    value = command['text']
    logger.info(f"set_chat_history_size!!!, value={value}")
    respond(text=DEFAULT_LOADING_TEXT)

    if value is None or value == "":
        respond(text="You must provide a value. eg /set_chat_history_size 10")
        return send_help_buttons(context.channel_id, client, "")

    save_s3("chat_history_size", value, logger, context, s3_client, AWS_STORAGE_BUCKET_NAME)
    respond(text=f"Default chat history size for queries set to: {value}")  # Respond to the command


def handle_predict_func(ack, command, respond, context, logger, client, payload):
    # Acknowledge command request
    ack()

    value = command['text']
    logger.info(f"predict!!!, value={value}")
    if not value:
        value = 2
    respond(text=DEFAULT_LOADING_TEXT)

    try:
        predict_table(context, client, payload, value)
    except Exception as e:
        traceback.print_exc()
        logger.exception(e)
        respond(text=f"Failed to run prediction")  # Respond to the command
        return send_help_buttons(context.channel_id, client, "")


def handle_suggest_tables_func(ack, command, respond, context, logger, client, payload):
    # Acknowledge command request
    ack()

    value = command['text']
    logger.info(f"handle_suggest_tables_func!!!, value={value}")
    if not value:
        respond(text="You must provide a query.")
        return send_help_buttons(context.channel_id, client, "")

    respond(text=DEFAULT_LOADING_TEXT)

    try:
        suggest_tables(context, client, payload, value)
    except Exception as e:
        traceback.print_exc()
        logger.exception(e)
        respond(text=f"Failed to run handle_suggest_tables_func")  # Respond to the command
        return send_help_buttons(context.channel_id, client, "")


def handle_show_queries_func(ack, command, respond, context, logger, client, payload):
    ack()
    api_key = context["api_key"]
    team_id = context.team_id
    user_id = context.user_id
    db_url = context.get("db_url")

    loading_text = fetch_data_from_genieapi(
        api_key=api_key,
        endpoint="/get_my_chat_history",
        team_id=team_id,
        user_id=user_id,
        resourcename=db_url
    )

    queries = loading_text["result"]

    # Building the select menu block
    options = [{"text": {"type": "plain_text",
                         "text": (query["question"][:72] + '...') if len(query["question"]) > 75 else query[
                             "question"]}, "value": str(query["id"])} for query in queries]
    blocks = [{
        "type": "section",
        "text": {"type": "mrkdwn", "text": "Select a question:"},
        "accessory": {
            "type": "static_select",
            "placeholder": {"type": "plain_text", "text": "Choose a question"},
            "options": options,
            "action_id": "query_selected"
        }
    }]

    respond(blocks=blocks)


def handle_set_debug_func(ack, command, respond, context: BoltContext, logger: logging.Logger, client,
                          s3_client,
                          AWS_STORAGE_BUCKET_NAME):
    # Acknowledge command request
    ack()

    value = command['text']
    logger.info(f"handle_set_debug_func!!!, value={value}")
    respond(text=DEFAULT_LOADING_TEXT)

    if value is None or value == "":
        respond(text="You must provide a value. eg /set_debug true")
        return send_help_buttons(context.channel_id, client, "")

    save_s3("debug", value, logger, context, s3_client, AWS_STORAGE_BUCKET_NAME)
    respond(text=f"Debug set to: {value}")  # Respond to the command


def handle_set_experimental_features_func(ack, command, respond, context: BoltContext, logger: logging.Logger, client,
                                          s3_client,
                                          AWS_STORAGE_BUCKET_NAME):
    # Acknowledge command request
    ack()

    value = command['text']
    logger.info(f"handle_set_experimental_features_func!!!, value={value}")
    respond(text=DEFAULT_LOADING_TEXT)

    if value is None or value == "":
        respond(text="You must provide a value. eg /set_experimental_features true")
        return send_help_buttons(context.channel_id, client, "")

    save_s3("experimental_features", value, logger, context, s3_client, AWS_STORAGE_BUCKET_NAME)
    respond(text=f"Experimental features set to: {value}")  # Respond to the command


def handle_set_db_warehouse_func(ack, command, respond, context: BoltContext, logger: logging.Logger, client,
                                 s3_client, AWS_STORAGE_BUCKET_NAME):
    # Acknowledge command request
    ack()

    value = command['text']
    logger.info(f"handle_set_warehouse!!!, value={value}")
    respond(text=DEFAULT_LOADING_TEXT)

    if value is None or value == "":
        respond(text="You must provide a value. eg /set_warehouse test123")
        return send_help_buttons(context.channel_id, client, "")

    save_s3("db_warehouse", value, logger, context, s3_client, AWS_STORAGE_BUCKET_NAME)
    respond(text=f"db_warehouse set to: {value}")  # Respond to the command


def handle_get_db_warehouses_func(ack, command, respond, context: BoltContext, logger: logging.Logger, client,
                                  payload: dict):
    # Acknowledge command request
    ack()

    logger.info(f"handle_get_db_warehouses_func!!!")
    respond(text=DEFAULT_LOADING_TEXT)

    api_key = context.get("api_key")
    db_url = context.get("db_url")

    value = command['text']

    if value == "":
        value = db_url

    if value is None or value == "":
        respond(
            text=f"Get DB Warehouses requires one argument Or a previously set DB with /use_db or /set_db_url")  # Respond to the command
        return send_help_buttons(context.channel_id, client, "")

    is_in_dm_with_bot = True
    messages = []
    user_id = context.actor_user_id or context.user_id

    try:
        loading_text = fetch_data_from_genieapi(api_key=api_key,
                                                endpoint="/list/user/database_connection/warehouses",
                                                resourcename=value)
        json_obj = loading_text["result"]
        blocks = []
        for c in json_obj:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"{c}"
                },
                "accessory": {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": f"Use {c} Warehouse"
                    },
                    "value": c,  # This will be passed to the action handler when clicked
                    "action_id": f"button:set_db_warehouse:{c}"
                }
            })
        respond(blocks=blocks)

        post_wip_message_with_attachment(
            client=client,
            channel=context.channel_id,
            thread_ts=payload.get("thread_ts") if is_in_dm_with_bot else payload["ts"],
            loading_text=loading_text,
            messages=messages,
            user=user_id,
            context=context,
        )

    except Exception as e:
        traceback.print_exc()
        logger.exception(e)
        respond(text=f"Failed to get DB Warehouses")  # Respond to the command
        return send_help_buttons(context.channel_id, client, "")


def handle_set_ai_model_func(ack, command, respond, context: BoltContext, logger: logging.Logger, client,
                             s3_client, AWS_STORAGE_BUCKET_NAME):
    # Acknowledge command request
    ack()

    value = command['text']
    logger.info(f"handle_set_ai_model!!!, value={value}")
    respond(text=DEFAULT_LOADING_TEXT)

    if value is None or value == "":
        respond(text="You must provide a value. eg /set_ai_model test123")
        return send_help_buttons(context.channel_id, client, "")

    save_s3("ai_model", value, logger, context, s3_client, AWS_STORAGE_BUCKET_NAME)
    respond(text=f"ai_model set to: {value}")  # Respond to the command


def handle_set_ai_temp_func(ack, command, respond, context: BoltContext, logger: logging.Logger, client,
                            s3_client, AWS_STORAGE_BUCKET_NAME):
    # Acknowledge command request
    ack()

    value = command['text']
    logger.info(f"handle_set_ai_temp!!!, value={value}")
    respond(text=DEFAULT_LOADING_TEXT)

    if value is None or value == "":
        respond(text="You must provide a value. eg /set_ai_temp test123")
        return send_help_buttons(context.channel_id, client, "")

    save_s3("ai_temp", value, logger, context, s3_client, AWS_STORAGE_BUCKET_NAME)
    respond(text=f"ai_temp set to: {value}")  # Respond to the command


def handle_query_selected_action(ack, context, client, payload, respond, id):
    ack()
    api_key = context["api_key"]
    team_id = context.team_id
    user_id = context.user_id

    respond(
        text=f":{DEFAULT_LOADING_TEXT},  chat_history_id={id}")  # Respond to the command

    loading_text = fetch_data_from_genieapi(
        api_key=api_key,
        endpoint="/get_my_chat_history",
        team_id=team_id,
        user_id=user_id,
        id=id,
        execute_sql=True,
    )

    is_in_dm_with_bot = True
    messages = []
    user_id = context.actor_user_id or context.user_id

    # Use the built-in WebClient to upload the file
    post_wip_message_with_attachment(
        client=client,
        channel=context.channel_id,
        thread_ts=payload.get("thread_ts") if is_in_dm_with_bot else payload["ts"],
        loading_text=loading_text,
        messages=messages,
        user=user_id,
        context=context,
    )


def handle_help_actions_func(ack, body, say):
    ack()  # Acknowledge the action

    # Get the specific help action (e.g., "datasets" from "help:datasets")
    help_topic = body["actions"][0]["action_id"].split(":")[1]

    # Depending on the topic, send the appropriate help message
    if help_topic == "datasets":
        say("Have you configured your database URL? If you haven't, you can configure your database URL by using /set_db_url. If you already configured your database, to view the tables in your database, please use the command /get_db_tables.  Check our manual: https://opengenie.gitbook.io/genie-ai-slack-bot/overview/data-bases-and-data-lakes-we-support")
    elif help_topic == "queries":
        say("Check frequently asked queries to see what other users have been querying  https://opengenie.gitbook.io/genie-ai-slack-bot/product-guides/f.a.q./queries Additionally, you can use the /suggest tool for inspiration on suggested questions to ask your data!")
    elif help_topic == "general":
        say("Here is our manual, where you can find setup instructions at this link: https://opengenie.gitbook.io/genie-ai-slack-bot/overview/what-genie-ai-can-do . If you encounter an error or bug, please contact our support team at help@opengenie.on.spiceworks.com . Our dedicated technical agents will promptly create a ticket and go the extra mile to resolve your issue")
    else:
        say("I'm here to help! How can I assist you?")


def save_s3(
        key: str,
        value: str,
        logger: logging.Logger,
        context: BoltContext,
        s3_client: boto3.client,
        AWS_STORAGE_BUCKET_NAME: str
):
    bucket_key = get_bucket_key(context, key, logger)

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
            traceback.print_exc()
            # If the object doesn't exist, create a new one
            data = {}

        # Step 2: Update or set the key and value in the object
        data[key] = value

        # Step 3: Put the updated or new object back into S3
        s3_client.put_object(
            Bucket=AWS_STORAGE_BUCKET_NAME,
            Key=bucket_key,
            Body=json.dumps(data)
        )
        return
    except botocore.exceptions.ClientError as e:
        traceback.print_exc()
        # Specific exception handling for boto3's client errors
        logger.error(f"save_s3, Encountered an error ClientError, with boto3: {e}")
        return
    except Exception as e:
        traceback.print_exc()
        logger.error(f"save_s3, Encountered an error Exception, with boto3: {e}")
        return


def delete_s3(
        key: str,
        logger: logging.Logger,
        context: BoltContext,
        s3_client: boto3.client,
        AWS_STORAGE_BUCKET_NAME: str
):
    bucket_key = get_bucket_key(context, key, logger)

    try:
        print(f"delete_s3, delete_object, bucket_key={bucket_key}")
        s3_client.delete_object(
            Bucket=AWS_STORAGE_BUCKET_NAME,
            Key=bucket_key,
        )
    except botocore.exceptions.ClientError as e:
        traceback.print_exc()
        # Specific exception handling for boto3's client errors
        logger.error(f"delete_s3, Encountered an error ClientError, with boto3: {e}")
        return
    except Exception as e:
        traceback.print_exc()
        logger.error(f"delete_s3, Encountered an error Exception, with boto3: {e}")
        return


def get_bucket_key(context, key, logger):
    user_id = context.actor_user_id or context.user_id
    if key == "db_table" \
            or key == "db_url" \
            or key == "db_schema" \
            or key == "db_warehouse" \
            or key == "ai_engine" \
            or key == "ai_model" \
            or key == "ai_temp" \
            or key == "debug" \
            or key == "experimental_features" \
            or key == "chat_history_size":
        bucket_key = context.team_id + "_" + user_id
    else:
        bucket_key = context.team_id
    logger.info(f"get_bucket_key, bucket_key={bucket_key}")
    return bucket_key
