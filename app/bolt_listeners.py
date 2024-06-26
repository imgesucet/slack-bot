import logging
import re
import time
import traceback

from openai.error import Timeout
from slack_bolt import App, Ack, BoltContext, BoltResponse
from slack_bolt.request.payload_utils import is_event
from slack_sdk.web import WebClient

from app.api_funcs import get_language_to_sql
from app.env import (
    OPENAI_TIMEOUT_SECONDS,
    SYSTEM_TEXT,
    TRANSLATE_MARKDOWN,
)
from app.i18n import translate
from app.openai_ops import (
    start_receiving_openai_response,
    format_openai_message_content,
    consume_openai_stream_to_write_reply,
    build_system_text,
    messages_within_context_window,
)
from app.slack_ops import (
    find_parent_message,
    is_no_mention_thread,
    post_wip_message,
    update_wip_message, post_wip_message_with_attachment,
)

from app.utils import redact_string, fetch_data_from_genieapi, DEFAULT_LOADING_TEXT, DEFAULT_ERROR_TEXT, \
    DEFAULT_ERROR_TEXT_AUTH, DEFAULT_ERROR_TEXT_ERR


#
# Listener functions
#


def just_ack(ack: Ack):
    ack()


POST_GRES_DICT = {}

TIMEOUT_ERROR_MESSAGE = (
    f":warning: Sorry! It looks like Genie didn't respond within {OPENAI_TIMEOUT_SECONDS} seconds. "
    "Please try again later. :bow:"
)

URL_PATTERN_POSTGRES = re.compile(
    r'^(?:http|ftp)s?://'  # http:// or https://
    r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|'  # domain...
    r'localhost|'  # localhost...
    r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}|'  # ...or IP
    r'postgres:\/\/[^\s\/$.?#].[^\s]*$)'  # PostgreSQL URL
    r'(?::\d+)?'  # optional port
    r'(?:/?|[/?]\S+)$', re.IGNORECASE)


def is_valid_url(string):
    return bool(re.match(URL_PATTERN_POSTGRES, string))


def extract_postgres_url(sentence):
    url_pattern = re.compile(
        r'postgres:\/\/[^\s\/$.?#].[^\s]*$', re.IGNORECASE)

    match = re.search(url_pattern, sentence)
    if match:
        return match.group(0)
    else:
        return None


def extract_table(sentence):
    url_pattern = re.compile(
        r'table:\/\/[^\s\/$.?#].[^\s]*$', re.IGNORECASE)

    match = re.search(url_pattern, sentence)
    if match:
        return match.group(0)
    else:
        return None


def respond_to_app_mention(
        context: BoltContext,
        payload: dict,
        client: WebClient,
        logger: logging.Logger,
):
    last_message = None
    if payload.get("thread_ts") is not None:
        parent_message = find_parent_message(
            client, context.channel_id, payload.get("thread_ts")
        )
        if parent_message is not None:
            if is_no_mention_thread(context, parent_message):
                # The message event handler will reply to this
                return

    wip_reply = None
    # Replace placeholder for Slack user ID in the system prompt
    system_text = build_system_text(SYSTEM_TEXT, TRANSLATE_MARKDOWN, context)
    messages = [{"role": "system", "content": system_text}]

    api_key = context.get("api_key")
    is_in_dm_with_bot = payload.get("channel_type") == "im"

    try:
        if api_key is None:
            client.chat_postMessage(
                channel=context.channel_id,
                text="To use this app, please configure your Genie API key first",
            )
            return

        user_id = context.actor_user_id or context.user_id

        if payload.get("thread_ts") is not None:
            # Mentioning the bot user in a thread
            replies_in_thread = client.conversations_replies(
                channel=context.channel_id,
                ts=payload.get("thread_ts"),
                include_all_metadata=True,
                limit=1000,
            ).get("messages", [])
            for reply in replies_in_thread:
                reply_text = redact_string(reply.get("text"))
                messages.append(
                    {
                        "role": (
                            "assistant"
                            if reply["user"] == context.bot_user_id
                            else "user"
                        ),
                        "content": (
                                f"<@{reply['user']}>: "
                                + format_openai_message_content(
                            reply_text, TRANSLATE_MARKDOWN
                        )
                        ),
                    }
                )
                last_message = reply_text

        else:
            # Strip bot Slack user ID from initial message
            msg_text = re.sub(f"<@{context.bot_user_id}>\\s*", "", payload["text"])
            msg_text = redact_string(msg_text)
            messages.append(
                {
                    "role": "user",
                    "content": f"<@{user_id}>: "
                               + format_openai_message_content(msg_text, TRANSLATE_MARKDOWN),
                }
            )
            last_message = msg_text

        text_query = last_message
        get_language_to_sql(
            context=context,
            client=client,
            payload=payload,
            messages=messages,
            logger=logger,
            text_query=text_query
        )


    except Timeout as e:
        traceback.print_exc()
        text = f"bolt_listeners.py, Timeout, Failed to process request: {e}"
        logger.exception(text)
        client.chat_postMessage(
            channel=context.channel_id,
            thread_ts=payload.get("thread_ts") if is_in_dm_with_bot else payload["ts"],
            text=DEFAULT_ERROR_TEXT,
        )
    except Exception as e:
        traceback.print_exc()
        text = f"bolt_listeners.py, Exception, Failed to process request: {e}"
        logger.exception(text)
        if f"{e}" == "USER_NOT_AUTHORIZED":
            client.chat_postMessage(
                channel=context.channel_id,
                thread_ts=payload.get("thread_ts") if is_in_dm_with_bot else payload["ts"],
                text=DEFAULT_ERROR_TEXT_AUTH,
            )
        else:
            client.chat_postMessage(
                channel=context.channel_id,
                thread_ts=payload.get("thread_ts") if is_in_dm_with_bot else payload["ts"],
                text=DEFAULT_ERROR_TEXT_ERR,
            )


def respond_to_new_message(
        context: BoltContext,
        payload: dict,
        client: WebClient,
        logger: logging.Logger,
):
    if payload.get("bot_id") is not None and payload.get("bot_id") != context.bot_id:
        # Skip a new message by a different app
        return

    is_in_dm_with_bot = payload.get("channel_type") == "im"
    try:

        messages_in_context = client.conversations_replies(
            channel=context.channel_id,
            ts=payload["ts"],
            include_all_metadata=True,
            limit=1000,
        ).get("messages", [])
        last_message = messages_in_context[-1]
        # print(f"--- the last message is :{last_message} \n")

        is_no_mention_required = False
        thread_ts = payload.get("thread_ts")
        if is_in_dm_with_bot is False and thread_ts is None:
            return

        api_key = context.get("api_key")
        if api_key is None:
            return

        messages_in_context = []
        if is_in_dm_with_bot is True and thread_ts is None:
            # In the DM with the bot
            past_messages = client.conversations_history(
                channel=context.channel_id,
                include_all_metadata=True,
                limit=100,
            ).get("messages", [])
            past_messages.reverse()
            # Remove old messages
            for message in past_messages:
                seconds = time.time() - float(message.get("ts"))
                if seconds < 86400:  # less than 1 day
                    messages_in_context.append(message)
            is_no_mention_required = True
        else:
            # In a thread with the bot in a channel
            messages_in_context = client.conversations_replies(
                channel=context.channel_id,
                ts=thread_ts,
                include_all_metadata=True,
                limit=1000,
            ).get("messages", [])
            if is_in_dm_with_bot is True:
                is_no_mention_required = True
            else:
                the_parent_message_found = False
                for message in messages_in_context:
                    if message.get("ts") == thread_ts:
                        the_parent_message_found = True
                        is_no_mention_required = is_no_mention_thread(context, message)
                        break
                if the_parent_message_found is False:
                    parent_message = find_parent_message(
                        client, context.channel_id, thread_ts
                    )
                    if parent_message is not None:
                        is_no_mention_required = is_no_mention_thread(
                            context, parent_message
                        )

        messages = []
        user_id = context.actor_user_id or context.user_id
        last_assistant_idx = -1
        indices_to_remove = []
        for idx, reply in enumerate(messages_in_context):
            maybe_event_type = reply.get("metadata", {}).get("event_type")
            if maybe_event_type == "chat-gpt-convo":
                if context.bot_id != reply.get("bot_id"):
                    # Remove messages by a different app
                    indices_to_remove.append(idx)
                    continue
                maybe_new_messages = (
                    reply.get("metadata", {}).get("event_payload", {}).get("messages")
                )
                if maybe_new_messages is not None:
                    if len(messages) == 0 or user_id is None:
                        new_user_id = (
                            reply.get("metadata", {})
                            .get("event_payload", {})
                            .get("user")
                        )
                        if new_user_id is not None:
                            user_id = new_user_id
                    messages = maybe_new_messages
                    last_assistant_idx = idx

        if is_no_mention_required is False:
            return

        if is_in_dm_with_bot is True or last_assistant_idx == -1:
            # To know whether this app needs to start a new convo
            if not next(filter(lambda msg: msg["role"] == "system", messages), None):
                # Replace placeholder for Slack user ID in the system prompt
                system_text = build_system_text(
                    SYSTEM_TEXT, TRANSLATE_MARKDOWN, context
                )
                messages.insert(0, {"role": "system", "content": system_text})

        filtered_messages_in_context = []
        for idx, reply in enumerate(messages_in_context):
            # Strip bot Slack user ID from initial message
            if idx == 0:
                reply["text"] = re.sub(
                    f"<@{context.bot_user_id}>\\s*", "", reply["text"]
                )
            if idx not in indices_to_remove:
                filtered_messages_in_context.append(reply)
        if len(filtered_messages_in_context) == 0:
            return

        for reply in filtered_messages_in_context:
            msg_user_id = reply.get("user")
            reply_text = redact_string(reply.get("text"))
            messages.append(
                {
                    "content": f"<@{msg_user_id}>: "
                               + format_openai_message_content(reply_text, TRANSLATE_MARKDOWN),
                    "role": "user",
                }
            )

        text_query = last_message["text"]
        get_language_to_sql(
            context=context,
            client=client,
            payload=payload,
            messages=messages,
            logger=logger,
            text_query=text_query
        )

    except Timeout as e:
        traceback.print_exc()
        text = f"bolt_listeners.py, Timeout, Failed to process request: {e}"
        logger.exception(text)
        client.chat_postMessage(
            channel=context.channel_id,
            thread_ts=payload.get("thread_ts") if is_in_dm_with_bot else payload["ts"],
            text=DEFAULT_ERROR_TEXT,
        )
    except Exception as e:
        traceback.print_exc()
        text = f"bolt_listeners.py, Exception, Failed to process request: {e}"
        logger.exception(text)
        if f"{e}" == "USER_NOT_AUTHORIZED":
            client.chat_postMessage(
                channel=context.channel_id,
                thread_ts=payload.get("thread_ts") if is_in_dm_with_bot else payload["ts"],
                text=DEFAULT_ERROR_TEXT_AUTH,
            )
        else:
            client.chat_postMessage(
                channel=context.channel_id,
                thread_ts=payload.get("thread_ts") if is_in_dm_with_bot else payload["ts"],
                text=f"{DEFAULT_ERROR_TEXT_ERR}, status",
            )


def register_listeners(app: App):
    app.event("app_mention")(ack=just_ack, lazy=[respond_to_app_mention])
    app.event("message")(ack=just_ack, lazy=[respond_to_new_message])


MESSAGE_SUBTYPES_TO_SKIP = ["message_changed", "message_deleted"]


# To reduce unnecessary workload in this app,
# this before_authorize function skips message changed/deleted events.
# Especially, "message_changed" events can be triggered many times when the app rapidly updates its reply.
def before_authorize(
        body: dict,
        payload: dict,
        logger: logging.Logger,
        next_,
):
    if (
            is_event(body)
            and payload.get("type") == "message"
            and payload.get("subtype") in MESSAGE_SUBTYPES_TO_SKIP
    ):
        logger.debug(
            "Skipped the following middleware and listeners "
            f"for this message event (subtype: {payload.get('subtype')})"
        )
        return BoltResponse(status=200, body="")
    next_()


def preview_table(context, client, payload, value):
    api_key = context["api_key"]
    db_url = context["db_url"]
    db_schema = context.get("db_schema")
    db_warehouse = context.get("db_warehouse")
    ai_engine = context.get("ai_engine")
    ai_model = context.get("ai_model")
    ai_temp = context.get("ai_temp")

    table_name = value
    text_query = f"get 10 sample rows for {table_name}"
    loading_text = fetch_data_from_genieapi(
        api_key=api_key,
        endpoint="/language_to_sql",
        text_query=text_query,
        table_name=table_name,
        resourcename=db_url,
        is_generate_code=False,
        db_schema=db_schema,
        ai_engine=ai_engine,
        ai_model=ai_model,
        ai_temp=ai_temp,
        db_warehouse=db_warehouse,
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


def suggest_table(context, client, payload, value):
    api_key = context["api_key"]
    db_url = context["db_url"]
    db_schema = context["db_schema"]

    table_name = value
    loading_text = fetch_data_from_genieapi(api_key=api_key,
                                            endpoint="/recommend_questions",
                                            table_name=table_name,
                                            resourcename=db_url,
                                            db_schema=db_schema,
                                            )

    is_in_dm_with_bot = True
    messages = []
    user_id = context.actor_user_id or context.user_id

    print(f"suggest_table, loading_text={loading_text}")

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


def predict_table(context, client, payload, value):
    api_key = context["api_key"]

    loading_text = fetch_data_from_genieapi(api_key=api_key,
                                            endpoint="/predict_questions",
                                            predict_count=value,
                                            team_id=context.team_id,
                                            user_id=context.user_id,
                                            )

    is_in_dm_with_bot = True
    messages = []
    user_id = context.actor_user_id or context.user_id

    print(f"predict_table, loading_text={loading_text}")

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


def suggest_tables(context, client, payload, value):
    api_key = context["api_key"]
    db_url = context["db_url"]

    loading_text = fetch_data_from_genieapi(api_key=api_key,
                                            endpoint="/identify_tables_for_query",
                                            text_query=value,
                                            resourcename=db_url,
                                            )

    is_in_dm_with_bot = True
    messages = []
    user_id = context.actor_user_id or context.user_id

    print(f"suggest_tables, loading_text={loading_text}")

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
