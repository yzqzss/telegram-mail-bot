# telegram-mail-bot

A Telegram bot that retrives the newest email periodically and sends them to you as chat messages.


![Python Version](https://img.shields.io/badge/python-3.6-blue.svg)
[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](https://opensource.org/licenses/MIT)

## Usage

### Setup
```
/add_email john.doe@example.com password protocol://server[:port] smtp_protocol://server[:port]
/list_email
/del_email john.doe@example.com
/help get help
```

Example:
```
/add_email john.doe@hotmail.com password pop3s://outlook.office365.com smtp+starttls://smtp-mail.outlook.com
/add_email john.doe@gmail.com password imaps://imap.gmail.com:993 smtps://smtp.gmail.com
```

You can then reply on received email to send reply with SMTP


## Deploy

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