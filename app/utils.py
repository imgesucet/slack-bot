import hashlib
import os
import re
import time

from urllib.parse import urlparse, urlunparse
from app.env import (
    REDACT_EMAIL_PATTERN,
    REDACT_PHONE_PATTERN,
    REDACT_CREDIT_CARD_PATTERN,
    REDACT_SSN_PATTERN,
    REDACT_USER_DEFINED_PATTERN,
    REDACTION_ENABLED,
)
import requests

DEFAULT_LOADING_TEXT = ":hourglass_flowing_sand: Wait a second, please ..."
DEFAULT_ERROR_TEXT = ":warning: No results were returned from your query. Please review the generated SQL and the associated table/schema, then try again."
DEFAULT_ERROR_TEXT_ERR = ":warning: We encountered an error while processing your query. Please review the generated SQL and the associated table/schema, then try again. If the issue persists, please contact Genie support."
DEFAULT_ERROR_TEXT_AUTH = ":warning: Your request was not authorized. Please review the installation steps, then try again."


def redact_string(input_string: str) -> str:
    """
    Redact sensitive information from a string (inspired by @quangnhut123)

    Args:
        - input_string (str): the string to redact

    Returns:
        - str: the redacted string
    """
    output_string = input_string
    if REDACTION_ENABLED:
        output_string = re.sub(REDACT_EMAIL_PATTERN, "[EMAIL]", output_string)
        output_string = re.sub(
            REDACT_CREDIT_CARD_PATTERN, "[CREDIT CARD]", output_string
        )
        output_string = re.sub(REDACT_PHONE_PATTERN, "[PHONE]", output_string)
        output_string = re.sub(REDACT_SSN_PATTERN, "[SSN]", output_string)
        output_string = re.sub(REDACT_USER_DEFINED_PATTERN, "[REDACTED]", output_string)

    return output_string


def get_space_travel_update(retry_count):
    messages = [
        "Compiling the latest AI algorithms... 🤖",
        "Analyzing quantum neural network patterns... 🌌",
        "Uploading terabytes of linguistic data... 💾",
        "Calibrating natural language understanding protocols... 📚",
        "Enhancing the predictive text synthesis module... 🔮",
        "Running diagnostics on the conversational context engine... 💬",
        "Training AI models with intergalactic language data... 🌐",
        "Optimizing response time for hyperspeed computation... ⚡",
        "Deploying humor algorithms for enhanced engagement... 😄",
        "Cross-referencing the universal encyclopedia of knowledge... 📖",
        "Fine-tuning the empathy and emotion detection circuits... ❤️",
        "Expanding the AI's creative idea generation unit... 🎨",
        "Testing the AI's joke generation module... 🤣",
        "Upgrading the AI's philosophical reasoning abilities... 🤔",
        "Synchronizing the AI's advanced problem-solving matrix... 🧠",
        "Implementing latest updates in ethical AI decision-making... ⚖️",
        "Incorporating the full spectrum of human history data... 🕒",
        "Enhancing the AI's cultural and social awareness... 🌍",
        "Preparing to deliver personalized, insightful responses... ✨",
        "Activating the advanced virtual reality imagination interface... 🌈"
    ]

    # Make sure the retry_count is within the range of available messages
    message_index = retry_count % len(messages)
    return messages[message_index]


def is_prime(number):
    if number <= 1:
        return False
    for i in range(2, int(number**0.5) + 1):
        if number % i == 0:
            return False
    return True

def fetch_data_from_genieapi(
        api_key=None,
        endpoint="/language_to_sql",
        text_query=None,
        table_name=None,
        resourcename=None,
        is_generate_code=None,
        chat_history_size=None,
        predict_count=None,
        team_id=None,
        user_id=None,
        db_schema=None,
        ai_engine=None,
        id=None,
        execute_sql=None,
        experimental_features=None,
        MAX_RETRIES=5,
        DELAY_FACTOR=0,
        client=None,
        channel=None,
        thread_ts=None,

):
    # Set defaults
    URL_DEFAULT = os.environ.get("GENIEAPI_HOST", "https://genieapi.defytrends.dev/api")

    if text_query is not None:
        text_query = text_query.replace('```', '').replace('`', '').strip()

    PARAMS_DEFAULT = {
        "text_query": text_query,
        "table_name": table_name,
        "execute_sql": True,
        "is_generate_code": True
    }

    # Use arguments if provided, otherwise default
    print(
        f"fetch_data_from_genieapi, api_key={api_key}, endpoint={endpoint}, text_query={text_query}, table_name={table_name}, resourcename={resourcename}, experimental_features={experimental_features}")
    endpoint_url = URL_DEFAULT + endpoint
    if resourcename is not None:
        PARAMS_DEFAULT["resourcename"] = resourcename
    if is_generate_code is not None:
        PARAMS_DEFAULT["is_generate_code"] = is_generate_code
    if chat_history_size is not None:
        PARAMS_DEFAULT["chat_history_size"] = chat_history_size
    if predict_count is not None:
        PARAMS_DEFAULT["predict_count"] = predict_count
    if team_id is not None:
        PARAMS_DEFAULT["slack_team_id"] = team_id
    if user_id is not None:
        PARAMS_DEFAULT["slack_user_id"] = user_id
    if db_schema:
        PARAMS_DEFAULT["db_schema"] = db_schema
    if ai_engine:
        PARAMS_DEFAULT["ai_engine"] = ai_engine
    if id:
        PARAMS_DEFAULT["id"] = id
    if execute_sql is not None:
        PARAMS_DEFAULT["execute_sql"] = execute_sql
    if experimental_features is not None:
        PARAMS_DEFAULT["is_experimental"] = experimental_features

    headers = {"X-API-Key": api_key}

    # Define max retries and delay for exponential backoff

    retries = 0
    while retries < MAX_RETRIES:
        response = requests.get(endpoint_url, headers=headers, params=PARAMS_DEFAULT)

        print(
            f"fetch_data_from_genieapi, response.status_code={response.status_code}, endpoint_url={endpoint_url}, headers={headers}, PARAMS_DEFAULT={PARAMS_DEFAULT}")

        # If status code is below 299, return the JSON response
        if response.status_code < 299:
            return response.json()

        elif 401 >= response.status_code <= 403:
            raise Exception("USER_NOT_AUTHORIZED")

        # If status code is 500 or above, retry the request
        else:
            retrymsg = get_space_travel_update(retries)
            isprime = is_prime(retries)
            # print(f"Will retry ?! retrymsg={retrymsg}, isprime={isprime}")
            if client and channel and thread_ts and isprime:
                client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text=retrymsg,
                )
            retries += 1
            if DELAY_FACTOR > 0:
                time.sleep(DELAY_FACTOR ** retries)  # exponential backoff
            else:
                time.sleep(10)

    # If maximum retries are reached, raise an exception
    raise Exception("Max retries reached without a successful response")


def post_data_to_genieapi(api_key=None, endpoint=None, params=None, post_body=None):
    # Set defaults
    API_KEY_DEFAULT = os.environ.get("API_KEY", "")
    URL_DEFAULT = os.environ.get("GENIEAPI_HOST", "https://genieapi.defytrends.dev/api")

    # PARAMS_DEFAULT = {
    #     "text_query": text_query,
    #     "table_name": table_name,
    #     "execute_sql": True
    # }

    # Use arguments if provided, otherwise default
    api_key = api_key if api_key is not None else API_KEY_DEFAULT

    endpoint_url = URL_DEFAULT + endpoint
    headers = {"X-API-Key": api_key}

    # Define max retries and delay for exponential backoff
    MAX_RETRIES = 3
    DELAY_FACTOR = 2

    retries = 0
    while retries < MAX_RETRIES:
        response = requests.post(endpoint_url, headers=headers, params=params, json=post_body)

        # If status code is below 299, return the JSON response
        if response.status_code < 299:
            return response.status_code

        # If status code is between 400 (inclusive) and 500 (exclusive), raise an exception and stop
        elif 400 <= response.status_code < 500:
            response.raise_for_status()

        # If status code is 500 or above, retry the request
        elif response.status_code >= 500:
            retries += 1
            time.sleep(DELAY_FACTOR ** retries)  # exponential backoff
        else:
            break

    # If maximum retries are reached, raise an exception
    raise Exception("Max retries reached without a successful response")


# Word lists, for the sake of this example we're using simple lists
# but you can expand them or use some cool/adjective word lists
ADJECTIVES = ["mystic", "silent", "bold", "ancient", "bright", "daring", "brave"]
NOUNS = ["river", "mountain", "forest", "sky", "ocean", "star", "cloud"]


def cool_name_generator(input_string):
    # Create an MD5 hash of the input
    hashed = hashlib.md5(input_string.encode()).hexdigest()

    # Convert some characters of the hash into integers for indexing
    adj_index = int(hashed[:2], 16) % len(ADJECTIVES)  # taking the first 2 characters
    noun_index = int(hashed[2:4], 16) % len(NOUNS)  # taking the next 2 characters

    # Use the indices to pick words from the lists
    name = f"{ADJECTIVES[adj_index]}-{NOUNS[noun_index]}"
    return name


def redact_credentials_from_url(url: None):
    if url is None:
        return url
    parsed = urlparse(url)

    # Replace the netloc part of the url
    if "@" in parsed.netloc:
        user_password, netloc = parsed.netloc.split("@", 1)
        redacted_netloc = f"REDACTED:REDACTED@{netloc}"
    else:
        redacted_netloc = parsed.netloc

    # Construct the redacted URL
    redacted_url = urlunparse((
        parsed.scheme,
        redacted_netloc,
        parsed.path,
        parsed.params,
        parsed.query,
        parsed.fragment
    ))

    return redacted_url


def send_help_buttons(channel_id, client, text):
    client.chat_postMessage(
        channel=channel_id,
        text=text,
        blocks=[
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "Need general assistance?"
                },
                "accessory": {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "Help"
                    },
                    "value": "help_button",  # Not sure if you need a value here, but I added one just in case
                    "action_id": "help:general"
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "Need assistance with datasets?"
                },
                "accessory": {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "Help with Datasets"
                    },
                    "value": "help_value",  # Optional value
                    "action_id": "help:datasets"
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "Need assistance with queries?"
                },
                "accessory": {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "Help with Queries"
                    },
                    "value": "help_value",  # Optional value
                    "action_id": "help:queries"
                }
            }
        ]
    )
