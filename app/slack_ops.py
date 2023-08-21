import json
from typing import Optional
from typing import List, Dict

from slack_sdk.web import WebClient, SlackResponse
from slack_bolt import BoltContext

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
) -> SlackResponse:
    try:
        sql = loading_text.get("sql_query", None)
    except KeyError:
        sql = None

    json_obj = loading_text["result"]
    table = json_to_slack_table(json_obj)

    data_string = json.dumps(json_obj, indent=4)  # 'your_data' is your JSON data

    with open('data.json', 'w') as file:
        file.write(data_string)

    with open('data.txt', 'w') as file:
        file.write(table)


    system_messages = [msg for msg in messages if msg["role"] == "system"]

    if sql is not None:
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=sql,
            metadata={
                "event_type": "chat-gpt-convo",
                "event_payload": {"messages": system_messages, "user": user},
            },
        )

    response = client.files_upload_v2(
        channels=channel,  # replace 'channel_id' with the ID of the channel you want to post to
        thread_ts=thread_ts,
        metadata={
            "event_type": "chat-gpt-convo",
            "event_payload": {"messages": system_messages, "user": user},
        },
        file="data.json",  # the path to your file
        filename="data.json"  # the filename that will be displayed in Slack
    )
    print(f"post_wip_message_with_attachment, data.json, response={response}")

    response = client.files_upload_v2(
        channels=channel,  # replace 'channel_id' with the ID of the channel you want to post to
        thread_ts=thread_ts,
        metadata={
            "event_type": "chat-gpt-convo",
            "event_payload": {"messages": system_messages, "user": user},
        },
        file="data.txt",  # the path to your file
        filename="data.txt"  # the filename that will be displayed in Slack
    )
    print(f"post_wip_message_with_attachment, data.txt, response={response}")


def json_to_slack_table(json_array):
    if not json_array:
        return '```No data available```'

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
    "To enable this app in this Slack workspace, you need to save your OpenAI API key. "
    "Visit <https://platform.openai.com/account/api-keys|your developer page> to grap your key!"
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
