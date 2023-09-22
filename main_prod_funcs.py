import logging

from app.utils import fetch_data_from_genieapi
from slack_bolt import BoltContext

from main_handlers import save_s3


def validate_api_key_registration(view: dict, context: BoltContext, logger: logging.Logger):
    logger.info("validate_api_key_registration, init")

    already_set_api_key = context.get("api_key")
    inputs = view["state"]["values"]
    api_key = inputs["api_key"]["input"]["value"]

    logger.info(f"validate_api_key_registration, init, already_set_api_key={already_set_api_key}")

    try:
        isauth = fetch_data_from_genieapi(api_key, "/isauth", None, None, None)
        if isauth["message"] != "ok":
            raise Exception("Invalid Genie API KEY")
    except Exception as e:
        text = "This API key seems to be invalid"
        logger.exception(e)
        raise Exception(text)


def save_api_key_registration(
        view: dict,
        logger: logging.Logger,
        context: BoltContext,
        s3_client, AWS_STORAGE_BUCKET_NAME
):
    logger.info("save_api_key_registration, init")
    inputs = view["state"]["values"]
    api_key = inputs["api_key"]["input"]["value"]
    # model = inputs["model"]["input"]["selected_option"]["value"]
    try:
        save_s3("api_key", api_key, logger, context, s3_client, AWS_STORAGE_BUCKET_NAME)
    except Exception as e:
        raise Exception(f"Failed to save Genie API KEY, e={e}")
