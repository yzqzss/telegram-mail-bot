# telegram-mail-bot

A Telegram bot that retrives the newest email periodically and sends them to you as chat messages.


![Python Version](https://img.shields.io/badge/python-3.6-blue.svg)
[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](https://opensource.org/licenses/MIT)

## Usage

### 1. Configure .env

Copy .env.template to .env, and modify it on your own

### 2. Start the server

#### Docker-Compose (recommended)

```
docker-compose up -d
```

- Note: use below command when updating
```
docker-compose up -d --build
```

#### Run Directly

```
./run.sh
```