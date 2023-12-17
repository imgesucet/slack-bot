from app.slack_ops import post_wip_message, post_wip_message_with_attachment
from app.utils import DEFAULT_LOADING_TEXT, fetch_data_from_genieapi


def get_language_to_sql(context, client, payload, messages, logger, text_query):
    api_key = context.get("api_key")
    db_table = context.get("db_table")

    table_name = context.get("db_table")
    db_url = context.get("db_url")
    db_schema = context.get("db_schema")
    ai_engine = context.get("ai_engine")
    ai_model = context.get("ai_model")
    ai_temp = context.get("ai_temp")
    experimental_features = context.get("experimental_features")
    chat_history_size = context.get("chat_history_size")
    db_warehouse = context.get("db_warehouse")
    is_in_dm_with_bot = payload.get("channel_type") == "im"
    user_id = context.actor_user_id or context.user_id

    post_wip_message(
        client=client,
        channel=context.channel_id,
        thread_ts=payload["ts"],
        loading_text=DEFAULT_LOADING_TEXT + f" db_url={db_url}, db_table={db_table}, db_schema={db_schema}, ai_engine={ai_engine}, experimental_features={experimental_features}",
        messages=messages,
        user=context.user_id,
    )

    logger.info(
        f"respond_to_new_message, fetch_data_from_genieapi, db_url={db_url}, table_name={table_name}, text_query={text_query}, chat_history_size={chat_history_size}")

    initial_request = fetch_data_from_genieapi(
        api_key=api_key,
        endpoint="/language_to_sql",
        text_query=text_query,
        table_name=table_name,
        resourcename=db_url,
        chat_history_size=chat_history_size,
        team_id=context.team_id,
        user_id=context.user_id,
        db_schema=db_schema,
        ai_engine=ai_engine,
        ai_model=ai_model,
        ai_temp=ai_temp,
        execute_sql=False,
        experimental_features=experimental_features,
        db_warehouse=db_warehouse
    )

    chat_history_id = initial_request.get("chat_history_id", None)

    client.chat_postMessage(
        channel=context.channel_id,
        thread_ts=payload["ts"],
        text=f"Genie is processing your request, id={chat_history_id}",
    )

    processing_sql = fetch_data_from_genieapi(
        api_key=api_key,
        endpoint="/language_to_sql_process",
        id=chat_history_id,
        chat_history_size=chat_history_size,
        experimental_features=experimental_features,
    )

    processing_sql_status = processing_sql.get("status", None)
    if processing_sql_status != "processing_sql":
        raise Exception("Max retries reached without a successful response")

    processing_sql = fetch_data_from_genieapi(
        api_key=api_key,
        endpoint="/language_to_sql_process",
        id=chat_history_id,
        chat_history_size=chat_history_size,
        experimental_features=experimental_features,
        client=client,
        channel=context.channel_id,
        thread_ts=payload["ts"],
        MAX_RETRIES=30,
        DELAY_FACTOR=0,
    )
    post_wip_message_with_attachment(
        client=client,
        channel=context.channel_id,
        thread_ts=payload["ts"],
        loading_text=processing_sql,
        messages=messages,
        user=user_id,
        context=context,
    )

    loading_text = fetch_data_from_genieapi(
        api_key=api_key,
        endpoint="/get_my_chat_history",
        id=chat_history_id,
        team_id=context.team_id,
        user_id=context.user_id,
        execute_sql=True,
        is_generate_code=True,
        MAX_RETRIES=30,
        DELAY_FACTOR=0
    )
    post_wip_message_with_attachment(
        client=client,
        channel=context.channel_id,
        thread_ts=payload["ts"],
        loading_text=loading_text,
        messages=messages,
        user=user_id,
        context=context,
    )
