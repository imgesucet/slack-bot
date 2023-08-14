import os
import re
import time

from app.env import (
    REDACT_EMAIL_PATTERN,
    REDACT_PHONE_PATTERN,
    REDACT_CREDIT_CARD_PATTERN,
    REDACT_SSN_PATTERN,
    REDACT_USER_DEFINED_PATTERN,
    REDACTION_ENABLED,
)
import requests


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



def fetch_data_from_genieapi(api_key=None, endpoint_url=None, text_query=None, table_name=None):
    # Set defaults
    API_KEY_DEFAULT = os.environ.get("API_KEY", "")
    URL_DEFAULT = os.environ.get("GENIEAPI_HOST", "https://genieapi.defytrends.dev")
    ENDPOINT = "/api/language_to_sql"

    PARAMS_DEFAULT = {
        "text_query": text_query,
        "table_name": table_name,
        "execute_sql": True
    }

    # Use arguments if provided, otherwise default
    api_key = api_key if api_key is not None else API_KEY_DEFAULT
    endpoint_url = endpoint_url if endpoint_url is not None else URL_DEFAULT

    endpoint_url = endpoint_url + ENDPOINT
    headers = {"X-API-Key": api_key}

    # Define max retries and delay for exponential backoff
    MAX_RETRIES = 3
    DELAY_FACTOR = 2

    retries = 0
    while retries < MAX_RETRIES:
        response = requests.get(endpoint_url, headers=headers, params=PARAMS_DEFAULT)

        # If status code is below 299, return the JSON response
        if response.status_code < 299:
            return response.json()

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




def post_data_to_genieapi(api_key=None, endpoint=None, params=None, post_body=None):
    # Set defaults
    API_KEY_DEFAULT = os.environ.get("API_KEY", "")
    URL_DEFAULT = os.environ.get("GENIEAPI_HOST", "https://genieapi.defytrends.dev")


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
