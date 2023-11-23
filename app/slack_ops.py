import base64
import io
import json
import os
from typing import Optional
from typing import List, Dict

from slack_sdk.web import WebClient, SlackResponse
from slack_bolt import BoltContext

from app.utils import DEFAULT_ERROR_TEXT


# ----------------------------
# General operations in a channel
# ----------------------------


def find_parent_message(
        client: WebClient, channel_id: Optional[str], thread_ts: Optional[str]
) -> Optional[dict]:
    if channel_id is None or thread_ts is None:
        return None

    messages = client.conversations_history(
        channel=channel_id,
        latest=thread_ts,
        limit=1,
        inclusive=1,
    ).get("messages", [])

    return messages[0] if len(messages) > 0 else None


def is_no_mention_thread(context: BoltContext, parent_message: dict) -> bool:
    parent_message_text = parent_message.get("text", "")
    return f"<@{context.bot_user_id}>" in parent_message_text


# ----------------------------
# WIP reply message stuff
# ----------------------------


def post_wip_message(
        *,
        client: WebClient,
        channel: str,
        thread_ts: str,
        loading_text: str,
        messages: List[Dict[str, str]],
        user: str,
) -> SlackResponse:
    system_messages = [msg for msg in messages if msg["role"] == "system"]
    return client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text=loading_text,
        metadata={
            "event_type": "chat-gpt-convo",
            "event_payload": {"messages": system_messages, "user": user},
        },
    )


def post_wip_message_with_attachment(
        *,
        client: WebClient,
        channel: str,
        thread_ts: str,
        loading_text: list,
        messages: List[Dict[str, str]],
        user: str,
        context: BoltContext,
):
    try:
        sql = loading_text.get("sql_query", None)
        score = loading_text.get("score", 0)
        json_obj = loading_text.get("result", None)
        base64_encoded_chart_image = loading_text.get("base64_encoded_chart_image", None)
        intermediate_steps = loading_text.get("intermediate_steps", [])
        ai_response = loading_text.get("ai_response", "")
        chat_history_id = loading_text.get("chat_history_id", "")
    except Exception as e:
        print(f"post_wip_message_with_attachment, error={e}")
        sql = None
        score = None
        json_obj = None
        base64_encoded_chart_image = None
        intermediate_steps = []
        ai_response = ""
        chat_history_id = ""

    debug = context.get("debug")
    chat_history_id_txt = f"id={chat_history_id}, "

    print(f"post_wip_message_with_attachment, base64_encoded_chart_image={base64_encoded_chart_image}")

    table = json_to_slack_table(json_obj)

    data_string = json.dumps(json_obj, indent=4)  # 'your_data' is your JSON data

    file_json = io.BytesIO(data_string.encode('utf-8')).getvalue()
    file_json_size = len(file_json)
    print(f"post_wip_message_with_attachment, file_json_size={file_json_size} bytes")

    file_txt = io.BytesIO(table.encode('utf-8')).getvalue()
    file_txt_size = len(file_txt)
    print(f"post_wip_message_with_attachment, file_txt_size={file_txt_size} bytes")

    if base64_encoded_chart_image:
        file_png = io.BytesIO(base64.b64decode(base64_encoded_chart_image)).getvalue()
        file_png_size = len(file_png)
        print(f"post_wip_message_with_attachment, file_png_size={file_png_size} bytes")
    else:
        file_png_size = 0
        file_png = None

    system_messages = [msg for msg in messages if msg["role"] == "system"]

    if ai_response or score:
        score_msg = ""
        if score > 0:
            score_msg = " The AI Calculated score for this answer is: " + str(score)
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=chat_history_id_txt + ai_response + score_msg,
            metadata={
                "event_type": "chat-gpt-convo",
                "event_payload": {"messages": system_messages, "user": user},
            },
        )

    if sql:
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=chat_history_id_txt + "```" + sql + "```",
            metadata={
                "event_type": "chat-gpt-convo",
                "event_payload": {"messages": system_messages, "user": user},
            },
        )

    if file_json_size > 0 and (json_obj and len(json_obj) > 0):
        client.files_upload_v2(
            channels=channel,  # replace 'channel_id' with the ID of the channel you want to post to
            thread_ts=thread_ts,
            metadata={
                "event_type": "chat-gpt-convo",
                "event_payload": {"messages": system_messages, "user": user},
            },
            content=file_json,
            filename=f"{chat_history_id}_data.json"  # the filename that will be displayed in Slack
        )
        print(f"post_wip_message_with_attachment, data.json, done")

    if file_txt_size > 0 and (json_obj and len(json_obj) > 0):
        client.files_upload_v2(
            channels=channel,  # replace 'channel_id' with the ID of the channel you want to post to
            thread_ts=thread_ts,
            metadata={
                "event_type": "chat-gpt-convo",
                "event_payload": {"messages": system_messages, "user": user},
            },
            content=file_txt,
            filename=f"{chat_history_id}_data.txt"  # the filename that will be displayed in Slack
        )
        print(f"post_wip_message_with_attachment, data.txt, done")

    if file_png_size > 0:
        client.files_upload_v2(
            channels=channel,  # replace 'channel_id' with the ID of the channel you want to post to
            thread_ts=thread_ts,
            metadata={
                "event_type": "chat-gpt-convo",
                "event_payload": {"messages": system_messages, "user": user},
            },
            content=file_png,
            filename=f"{chat_history_id}_data.png"  # the filename that will be displayed in Slack
        )

    if debug == "true" and len(intermediate_steps) > 0:
        intermediate_steps_table = json.dumps(intermediate_steps, indent=4)  # 'your_data' is your JSON data
        intermediate_steps_table_file_txt = io.BytesIO(intermediate_steps_table.encode('utf-8')).getvalue()
        client.files_upload_v2(
            channels=channel,  # replace 'channel_id' with the ID of the channel you want to post to
            thread_ts=thread_ts,
            metadata={
                "event_type": "chat-gpt-convo",
                "event_payload": {"messages": system_messages, "user": user},
            },
            content=intermediate_steps_table_file_txt,
            filename=f"{chat_history_id}_intermediate_steps.txt"  # the filename that will be displayed in Slack
        )
        print(f"post_wip_message_with_attachment, intermediate_steps.txt, done")

    # ERROR MSG
    if (len(sql) == 0 or not sql) and (len(json_obj) == 0 or not json_obj):
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=chat_history_id_txt + DEFAULT_ERROR_TEXT,
            metadata={
                "event_type": "chat-gpt-convo",
                "event_payload": {"messages": system_messages, "user": user},
            },
        )

    print(f"post_wip_message_with_attachment, data.png, done")


def json_to_slack_table(json_array):
    if not json_array:
        return '```No data available```'
    try:
        headers = list(json_array[0].keys())
        table_data = [headers]

        for json_object in json_array:
            row_data = [str(json_object[header]) for header in headers]
            table_data.append(row_data)

        # Transpose data for column-wise length calculation
        transposed_data = list(map(list, zip(*table_data)))
        column_widths = [max(len(str(word)) for word in col) for col in transposed_data]

        # Format the table
        table_string = "```\n"  # start with opening ```
        for row in table_data:
            row_string = '| ' + ' | '.join(f"{x:<{y}}" for x, y in zip(row, column_widths)) + ' |\n'
            table_string += row_string
        table_string += "```"  # end with closing ```
        return table_string
    except Exception as e:
        print(f"json_to_slack_table, error={e}")
        return ""


def update_wip_message(
        client: WebClient,
        channel: str,
        ts: str,
        text: str,
        messages: List[Dict[str, str]],
        user: str,
) -> SlackResponse:
    system_messages = [msg for msg in messages if msg["role"] == "system"]
    return client.chat_update(
        channel=channel,
        ts=ts,
        text=text,
        metadata={
            "event_type": "chat-gpt-convo",
            "event_payload": {"messages": system_messages, "user": user},
        },
    )


# ----------------------------
# Home tab
# ----------------------------

DEFAULT_HOME_TAB_MESSAGE = (
    "To enable this app in this Slack workspace, you need to save your Genie API key. "
    "Visit <https://opengenie.ai/|developer page> to grap your key!"
)

DEFAULT_HOME_TAB_CONFIGURE_LABEL = "Configure"


def build_home_tab(message: str, configure_label: str) -> dict:
    return {
        "type": "home",
        "blocks": [
            {
                "dispatch_action": True,
                "type": "input",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "plain_text_input-action",
                },
                "label": {"type": "plain_text", "text": "Label", "emoji": True},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": message,
                },
                "accessory": {
                    "action_id": "configure",
                    "type": "button",
                    "text": {"type": "plain_text", "text": configure_label},
                    "style": "primary",
                    "value": "api_key",
                },
            },

        ],
    }
