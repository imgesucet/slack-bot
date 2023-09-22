import json
import logging
import botocore

import boto3 as boto3
from slack_bolt import BoltContext
from app.bolt_listeners import DEFAULT_LOADING_TEXT, suggest_table, preview_table
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
                context["db_type"] = config.get("db_type")
            else:
                # The legacy data format
                context["OPENAI_MODEL"] = DEFAULT_OPENAI_MODEL
                context["OPENAI_TEMPERATURE"] = DEFAULT_OPENAI_TEMPERATURE
        except s3_client.exceptions.NoSuchKey as e:
            logger.error(f"set_s3_openai_api_key, team_id+user_id, key={key}, error={e}")

        context["OPENAI_API_TYPE"] = DEFAULT_OPENAI_API_TYPE
        context["OPENAI_API_BASE"] = DEFAULT_OPENAI_API_BASE
        context["OPENAI_API_VERSION"] = DEFAULT_OPENAI_API_VERSION
        context["OPENAI_DEPLOYMENT_ID"] = DEFAULT_OPENAI_DEPLOYMENT_ID
    except:  # noqa: E722
        context["api_key"] = None
    next_()


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
        logger.exception(e)
        respond(text=f"Failed to run preview for table")  # Respond to the command


def handle_get_db_tables_func(ack, command, respond, context: BoltContext, logger: logging.Logger, client,
                              payload: dict):
    # Acknowledge command request
    ack()

    logger.info(f"get_db_tables!!!")
    respond(text=DEFAULT_LOADING_TEXT)

    api_key = context["api_key"]
    db_url = context["db_url"]
    value = command['text']

    if value == "":
        value = db_url

    if value is None or value == "":
        respond(text=f"Get DB Tables requires one argument.")  # Respond to the command
        return send_help_buttons(context.channel_id, client, "")

    is_in_dm_with_bot = True
    messages = []
    user_id = context.actor_user_id or context.user_id

    try:
        loading_text = fetch_data_from_genieapi(api_key=api_key,
                                                endpoint="/list/user/database_connection/tables",
                                                resourcename=value)
        post_wip_message_with_attachment(
            client=client,
            channel=context.channel_id,
            thread_ts=payload.get("thread_ts") if is_in_dm_with_bot else payload["ts"],
            loading_text=loading_text,
            messages=messages,
            user=user_id,
        )

    except Exception as e:
        logger.exception(e)
        respond(text=f"Failed to get DB tables")  # Respond to the command
        return send_help_buttons(context.channel_id, client, "")


def handle_set_db_url_func(ack, command, respond, context: BoltContext, logger: logging.Logger, client, s3_client,
                           AWS_STORAGE_BUCKET_NAME):
    # Acknowledge command request
    ack()

    value = command['text']
    logger.info(f"set_db_url!!!, value={value}")
    respond(text=DEFAULT_LOADING_TEXT)

    if value is None or value == "":
        respond(
            text="You must provide the DB URL after /set_db_url [postgres://{user}:{password}@{host}:{port}/{db_name}?sslmode=require]")
        return send_help_buttons(context.channel_id, client, "")

    api_key = context["api_key"]
    db_type = context["db_type"]
    try:
        resource_name = cool_name_generator(value)
        post_data_to_genieapi(api_key, "/update/user/database_connection", None,
                              {"connection_string_url": value, "resourcename": resource_name, "db_type": db_type})

        save_s3("db_url", resource_name, logger, context, s3_client, AWS_STORAGE_BUCKET_NAME)
        respond(text=f"DB URL set to: {redact_string(resource_name)}")  # Respond to the command

    except Exception as e:
        logger.exception(e)
        respond(text=f"Failed to set DB URL to: {redact_string(value)}")  # Respond to the command
        return send_help_buttons(context.channel_id, client, "")


def handle_get_db_urls_func(ack, respond, context: BoltContext, logger: logging.Logger, client):
    # Acknowledge command request
    ack()

    logger.info(f"get_db_urls!!!")
    respond(text=DEFAULT_LOADING_TEXT)

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


def handle_use_db_func(ack, command, respond, context: BoltContext, logger: logging.Logger, client, s3_client,
                       AWS_STORAGE_BUCKET_NAME):
    # Acknowledge command request
    ack()

    value = command['text']
    logger.info(f"use_db!!!, value={value}")
    respond(text=DEFAULT_LOADING_TEXT)

    if value is None or value == "":
        respond(text="You must provide the DB alias after. eg /use_db bold-sky")
        return send_help_buttons(context.channel_id, client, "")

    save_s3("db_url", value, logger, context, s3_client, AWS_STORAGE_BUCKET_NAME)
    respond(text=f"Default DB for queries set to: {value}")  # Respond to the command


def handle_help_actions_func(ack, body, say):
    ack()  # Acknowledge the action

    # Get the specific help action (e.g., "datasets" from "help:datasets")
    help_topic = body["actions"][0]["action_id"].split(":")[1]

    # Depending on the topic, send the appropriate help message
    if help_topic == "datasets":
        say("Here's how to use the datasets ...")
    elif help_topic == "queries":
        say("Here's how to make a query ...")
    elif help_topic == "general":
        say("Here's some helpful information to assist you. [Describe the process or steps here.]")
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
    user_id = context.actor_user_id or context.user_id
    if key == "db_table" \
            or key == "db_url" \
            or key == "db_type":
        bucket_key = context.team_id + "_" + user_id
    else:
        bucket_key = context.team_id
    logger.info(f"save_s3, init, bucket_key={bucket_key}")

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
            Key=bucket_key,
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
