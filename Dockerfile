FROM python:3.11.3-slim-buster as builder
COPY requirements.txt /build/
WORKDIR /build/
RUN pip install -U pip && pip install -r requirements.txt

FROM python:3.11.3-slim-buster as app

RUN addgroup --system django \
    && adduser --system --ingroup django django

WORKDIR /app/
RUN chown django:django /app/
COPY --chown=django:django *.py /app/

RUN mkdir /app/app/
RUN chown django:django /app/app/

COPY --chown=django:django app/*.py /app/app/
COPY --chown=django:django --from=builder /usr/local/bin/ /usr/local/bin/
COPY --chown=django:django --from=builder /usr/local/lib/ /usr/local/lib/

USER django

ENTRYPOINT python main.py

# docker build . -t your-repo/chat-gpt-in-slack
# export SLACK_APP_TOKEN=xapp-...
# export SLACK_BOT_TOKEN=xoxb-...
# export OPENAI_API_KEY=sk-...
# docker run -e SLACK_APP_TOKEN=$SLACK_APP_TOKEN -e SLACK_BOT_TOKEN=$SLACK_BOT_TOKEN -e OPENAI_API_KEY=$OPENAI_API_KEY -it your-repo/chat-gpt-in-slack
