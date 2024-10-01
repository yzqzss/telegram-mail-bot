FROM docker.io/python:3.11 AS builder

# Install pdm
RUN pip install pdm

WORKDIR /workdir

ADD pyproject.toml pdm.lock /workdir/

ENV PDM_NON_INTERACTIVE=1

RUN pdm install --frozen-lockfile --production

######### Build Stage Finished ##########

FROM docker.io/python:3.11 AS runtime

RUN mkdir -pv /workdir/.venv

COPY --from=builder /workdir/.venv/ /workdir/.venv/

WORKDIR /workdir/
VOLUME /workdir/conf

ADD . /workdir/src

CMD ["./.venv/bin/python", "src/bot.py"]
