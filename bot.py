import asyncio
import datetime
import logging
import os
import re
import socket
import time
from traceback import format_exc
from typing import Any, Dict, Type
import dataclasses

from telegram import Update
from telegram.ext import (Application, CommandHandler, MessageHandler, ContextTypes, filters)
from pysondb import db as pysondb
from utils import EmailClientBase, EmailClientIMAP, EmailClientPOP3
from utils.oauth2_helper import OAuth2_MS, OAuth2Factory
from utils.smtpclient import send_email

socket.setdefaulttimeout(10) # avoid imaplib timeout

emailDB = pysondb.getDb("conf/email_accounts.json")

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s:%(lineno)d - %(message)s',
                    # stream=sys.stdout,
                    level=logging.INFO)
logger = logging.getLogger(__name__)


def getconf(x):
    return os.environ.get(x) # or dotenv_values().get(x)

BOT_TOKEN = getconf('TELEGRAM_TOKEN')
if not BOT_TOKEN:
    raise Exception('TELEGRAM_TOKEN not set in env or .env file')
_owner_chat_id = getconf('OWNER_CHAT_ID')
if not _owner_chat_id:
    raise Exception('OWNER_CHAT_ID not set in env or .env file')
OWNER_CHAT_ID = int(_owner_chat_id)
POLL_INTERVAL = int(getconf('POLL_INTERVAL') or '60')
ERR_REPORT_INTERVAL = int(getconf('ERR_REPORT_INTERVAL') or '3600')
REQUEST_TIMEOUT = int(getconf('REQUEST_TIMEOUT') or '60')
MAX_MESSAGE_LENGTH = 4096

def is_owner(update: Update) -> bool:
    if update.message:
        if update.message.chat_id == OWNER_CHAT_ID:
            return True
        if update.message.from_user is not None and update.message.from_user.id == OWNER_CHAT_ID:
            return True
    return False

def handle_large_text(text):
    while text:
        if len(text) < MAX_MESSAGE_LENGTH:
            yield text
            text = None
        else:
            out = text[:MAX_MESSAGE_LENGTH]
            yield out
            text = text.lstrip(out)

def error(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log Errors caused by Updates."""
    logger.warning('Update "%s" caused error "%s"', update, context.error)

async def _help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        return
    """Send a message when the command /help is issued."""
    help_str = f"""邮箱设置:
/add_email john.doe@example.com password protocol://server[:port] smtp_protocol://smtp_server[:port]
例：
    /add_email john.doe@hotmail.com P@ssw0rd pop3s://outlook.office365.com smtp+starttls://smtp-mail.outlook.com
    /add_email john.doe@hotmail.com token:ms:XX_refresh_token_XXX imaps://outlook.office365.com smtp+starttls://smtp-mail.outlook.com
    /add_email john.doe@hotmail.com code:ms:XX_authorization_code_XXX imaps://outlook.office365.com smtp+starttls://smtp-mail.outlook.com
    /add_email john.doe@gmail.com password imaps://imap.gmail.com:993 smtps://smtp.gmail.com
    
    use this URL to get auth code: https://login.microsoftonline.com/common/oauth2/v2.0/authorize?response_type=code&client_id={OAuth2_MS.client_id}&redirect_uri=https%3A%2F%2Flocalhost&scope=https%3A%2F%2Foutlook.office.com%2FIMAP.AccessAsUser.All+https%3A%2F%2Foutlook.office.com%2FPOP.AccessAsUser.All+https%3A%2F%2Foutlook.office.com%2FSMTP.Send+offline_access

/list_email
/del_email john.doe@example.com
/help get help

Telegram中回复即可直接回复邮件
"""
    assert update.message
    await update.message.reply_text(help_str)

@dataclasses.dataclass
class EmailConf():
    email_addr: str
    email_passwd: str
    server_uri: str
    smtp_server_uri: str | None
    chat_id: int
    reply_to_thread_id: int | None
    inbox_num: int

async def setting_list_email(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        return

    assert update.message
    msg = 'Email Account List:\n'
    for emailConfDict in emailDB.getAll():
        try:
            emailConf = getEmailConfFromDict(emailConfDict)
        except Exception:
            msg += "    (Invalid Email Account: %s)\n" % emailConfDict
            continue
        msg += f"    Email: {emailConf.email_addr}, Password: {len(emailConf.email_passwd) * '*'}, Server: {emailConf.server_uri}, SMTP Server: {emailConf.smtp_server_uri}, InboxNum: {emailConf.inbox_num}\n"
    
    await update.message.reply_text(msg)

async def setting_add_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        return
    assert update.message
    if not context.args:
        await update.message.reply_text("Invalid command!")
        return
    email_addr = context.args[0]
    email_passwd = context.args[1]
    email_server = context.args[2]
    email_smtp = None
    if len(context.args) > 3:
        email_smtp = context.args[3]
    
    if not email_server.startswith('imap') and not email_server.startswith('pop3'):
        await update.message.reply_text(f"invalid server: {email_server}")
        return
    if email_smtp and not email_smtp.startswith('smtp'):
        await update.message.reply_text(f"invalid smtp server: {email_smtp}")
        return
    
    
    message_thread_id = None
    """ 是否发送到指定的topic """
    if update.message.is_topic_message and update.message.message_thread_id:
        message_thread_id = update.message.message_thread_id

    emailConf = EmailConf(
        email_addr=email_addr,
        email_passwd=email_passwd,
        server_uri=email_server,
        smtp_server_uri=email_smtp,
        chat_id=update.message.chat_id,
        reply_to_thread_id=message_thread_id,
        inbox_num=-1,
    )
    
    logger.info("received setting_email command.")
    
    new_passwd = OAuth2Factory.code_to_token(s=email_passwd)
    if new_passwd:
        await update.message.reply_text(f"Exchanged refresh_token {new_passwd} from {email_passwd} for email {email_addr}, Rewriting password~")
        emailConf.email_passwd = email_passwd = new_passwd

    with getEmailClient(emailConf) as client:
        inbox_num = client.get_mails_count()
    emailConf.inbox_num = inbox_num
    
    if emailDB.getByQuery({'email_addr': email_addr}):
        await update.message.reply_text(f"Email {email_addr} is already configured! Overriding...")
        emailDB.updateByQuery({'email_addr': email_addr},  dataclasses.asdict(emailConf))
    else:
        emailDB.add(dataclasses.asdict(emailConf))
    
    await update.message.reply_text("Configure email success!")


async def setting_del_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        return
    assert update.message
    if not context.args:
        await update.message.reply_text("Invalid command!")
        return
    email_addr = context.args[0]
    
    emails = emailDB.getByQuery({'email_addr': email_addr})
    if not emails:
        await update.message.reply_text(f'cannot find email account: {email_addr}')
        return
    assert len(emails) == 1
    pk = emails[0][emailDB.id_fieldname]
    assert emailDB.deleteById(pk)
    await update.message.reply_text(f'Successfully deleted email account {email_addr}')

async def safeSendText(sender: Any, content: str):
    for text in handle_large_text(content):
        await safeSend(sender, text)

async def safeSend(sender: Any, content: str):
    for i in range(10):
        try:
            await sender(content)
            break
        except Exception:
            logger.warning('cannot send tg msg (retry %d)', i, exc_info=True)
        await asyncio.sleep(i * 5)

def getEmailConf(email_addr):
    emailConfs = emailDB.getByQuery({'email_addr': email_addr})
    if not emailConfs:
        raise Exception(f'cannot find config for email {email_addr}')
    assert len(emailConfs) == 1
    emailConfDict = emailConfs[0]
    if 'id' in emailConfDict:
        emailConfDict.pop('id')
    emailConf = EmailConf(**emailConfDict)
    return emailConf

def getEmailConfFromDict(emailConfDict):
    if 'id' in emailConfDict:
        emailConfDict.pop('id')
    emailConf = EmailConf(**emailConfDict)
    return emailConf

emailClientCache: dict[tuple, EmailClientBase] = {}
def getEmailClient(emailConf: EmailConf) -> EmailClientBase:
    cacheKey = tuple(dataclasses.astuple(emailConf))
    emailClient: EmailClientBase | None = emailClientCache.get(cacheKey, None)
    if emailClient:
        try:
            emailClient.refresh_connection()
            logger.info(f'email client for {emailConf.email_addr} is still good')
            return emailClient
        except Exception as e:
            logger.info(f'email client for {emailConf.email_addr} is invalid ({str(e)}), re-creating...')
            emailClient = None

    EmailClient: Type[EmailClientBase]
    if emailConf.server_uri.startswith('pop3'):
        EmailClient = EmailClientPOP3
    elif emailConf.server_uri.startswith('imap'):
        EmailClient = EmailClientIMAP
    else:
        raise Exception(f"invalid email server_uri: {emailConf.server_uri}")
    
    emailClient = EmailClient(emailConf.email_addr, emailConf.email_passwd, emailConf.server_uri)
    emailClientCache[cacheKey] = emailClient
    return emailClient

PERIODIC_TASK_ERRORS: dict[str, dict[str, list[str]]] = {
    
}
PERIODIC_TASK_TICK = 0
async def periodic_task(context: ContextTypes.DEFAULT_TYPE) -> None:
    # {
    #     'email_addr': email_addr,
    #     'email_passwd': email_passwd,
    #     'server_uri': email_server,
    #     'chat_id': update.message.chat_id,
    # }
    global PERIODIC_TASK_TICK
    logger.info("entered periodic task, tick: %d...", PERIODIC_TASK_TICK)
    PERIODIC_TASK_TICK += 1
    
    async def handler(emailConfDict: Dict, managers=None):
        logger.info("processing periodic task for %s", emailConfDict)
        try:
            if 'id' in emailConfDict:
                emailConfDict.pop('id')
            emailConf = EmailConf(**emailConfDict)
            email_addr = emailConf.email_addr
        except Exception:
            logger.warning('Cannot parse emailConfDict: %s', emailConfDict, exc_info=True)
            return

        def run_with_timeout_sync(fun, *args, **kwargs):
            fut = ThreadPool().apply_async(fun, args=args, kwds=kwargs)
            return fut.get(REQUEST_TIMEOUT)

        async def run_with_timeout(fun, *args, **kwargs):
            if not asyncio.iscoroutinefunction(fun):
                return run_with_timeout_sync(fun, *args, **kwargs)
            try:
                return await asyncio.wait_for(fun(*args, **kwargs), timeout=REQUEST_TIMEOUT)
            except asyncio.TimeoutError:
                raise Exception(f"Timeout after {REQUEST_TIMEOUT} seconds")

        try:
            client = getEmailClient(emailConf)
            new_inbox_num = await run_with_timeout(client.get_mails_count)
            if new_inbox_num > emailConf.inbox_num:
                for idx in range(emailConf.inbox_num + 1, new_inbox_num + 1):
                    try:
                        mail = await run_with_timeout(client.get_mail_by_index, idx)
                    except Exception:
                        logger.warning('cannot retrieve mail %d for %s', idx, emailConf, exc_info=True)
                        break
                    
                    text = f'''New Email [{emailConf.email_addr}-{idx}]\n'''
                    emailbody, emailfiles = mail.format_email()
                    text += emailbody

                    await run_with_timeout(safeSendText, lambda text: context.bot.send_message(chat_id=emailConf.chat_id, reply_to_message_id=emailConf.reply_to_thread_id,text=text), text)
                    for filename, filemime, file_content in emailfiles:
                        if filemime.startswith('image'):
                            await run_with_timeout(safeSend, lambda text: context.bot.send_document(chat_id=emailConf.chat_id, reply_to_message_id=emailConf.reply_to_thread_id, document=text, filename=filename), text)
                        else:
                            await run_with_timeout(safeSend, lambda text: context.bot.send_photo(chat_id=emailConf.chat_id, reply_to_message_id=emailConf.reply_to_thread_id, photo=file_content, filename=filename), text)
                    emailDB.updateByQuery({'email_addr': email_addr}, {'inbox_num': idx})
        except Exception as e:
            if re.findall(r'\bEOF\b', str(e)):
                pass # do not process occasional random network issue
            elif re.findall(r'Server Unavailable. 21', str(e)):
                pass # ignore stupid outlook server error
            else:
                if email_addr not in PERIODIC_TASK_ERRORS:
                    PERIODIC_TASK_ERRORS[email_addr] = {}
                exceptionStr = str(e)
                if exceptionStr not in PERIODIC_TASK_ERRORS[email_addr]:
                    PERIODIC_TASK_ERRORS[email_addr][exceptionStr] = []
                PERIODIC_TASK_ERRORS[email_addr][exceptionStr].append(format_exc())
            logger.warning('periodic task error in %s', email_addr, exc_info=True)
            logger.warning('periodic task error in %s', email_addr, exc_info=True)
    
            logger.warning('periodic task error in %s', email_addr, exc_info=True)    
    
    # for emailConfDict in emailDB.getAll():
    #     handler(emailConfDict)
    
    # TODO: Implement timeout control
    from multiprocessing.pool import ThreadPool
    tasks = [handler(emailConfDict) for emailConfDict in emailDB.getAll()]
    await asyncio.gather(*tasks)

LAST_ERROR_REPORT_TIME: float | None = None
LAST_ERROR_REPORT_TICK = 0

async def periodic_task_error_report(context: ContextTypes.DEFAULT_TYPE) -> None:
    global PERIODIC_TASK_ERRORS, LAST_ERROR_REPORT_TIME, LAST_ERROR_REPORT_TICK
    
    last_errors = PERIODIC_TASK_ERRORS
    logger.info("last errors (%s - %s): %s", LAST_ERROR_REPORT_TIME, time.time(), last_errors)
    
    queries = PERIODIC_TASK_TICK - LAST_ERROR_REPORT_TICK
    if LAST_ERROR_REPORT_TIME is not None:
        time_since_last_report = str(datetime.datetime.now() - datetime.datetime.fromtimestamp(LAST_ERROR_REPORT_TIME))
    else:
        time_since_last_report = str(datetime.timedelta(seconds=ERR_REPORT_INTERVAL))
    
    LAST_ERROR_REPORT_TICK = PERIODIC_TASK_TICK
    LAST_ERROR_REPORT_TIME = time.time()
    PERIODIC_TASK_ERRORS = {}
    
    if not last_errors:
        logger.info("No errors since last error report, skipping this report!")
        return
    
    text = ''
    for acc, accErrDict in last_errors.items():
        if sum(len(errList) for errList in accErrDict.values()) < queries * 0.5:
            # to avoid spamming, we only print error summary if this account has MASSIVE amount of errors
            text += f'Acc: {acc}\n'
            for errType, errList in accErrDict.items():
                text += f'    Error: {errType}, triggered {len(errList)} times\n'
    if not text:
        logger.info("No account have massive errors, skipping this report!")
    else:
        text = f'''Error Summary during last {queries} queries in duration {time_since_last_report}:\n''' + text
        await safeSendText(lambda text: context.bot.send_message(chat_id=OWNER_CHAT_ID, text=text), text)

def is_reply_to_bot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    assert update.message
    if update.message.reply_to_message:
        if update.message.reply_to_message.from_user:
            if update.message.reply_to_message.from_user.id == context.bot.id:
                return True
    return False

async def handle_reply_send_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message
    
    if not update.message.reply_to_message:
        return
    
    if not is_reply_to_bot(update, context):
        return

    original_message = update.message.reply_to_message.text
    if not original_message:
        return

    email, mail_id, from_email = re.findall(r'^.*?\[(.*?)-(\d+)\]\n[\S\s]+?From: .*?(\S+)\n', original_message)[0]
    reply_message = update.message.text
    if not reply_message:
        return

    subject, split, body = reply_message.partition('\n\n')
    if split != '\n\n':
        await update.message.reply_text("Don't know the subject of email. Send the email in this form: \n\n(Your subject here)\n\n(Your body)")
        return
    emailConf = getEmailConf(email)
    if not emailConf.smtp_server_uri:
        await update.message.reply_text(f"Cannot send email from {email}, no smtp server configured!")
        return
    send_email(
        smtp_server_uri=emailConf.smtp_server_uri, 
        sender_email=emailConf.email_addr, 
        password=emailConf.email_passwd, 
        receiver_email=from_email, 
        subject=subject, body=body)
    await update.message.reply_text(f"Successfully sent the email from {email} to {from_email} with subject {subject}")

def main():
    # Create the EventHandler and pass it your bot's token.
    assert BOT_TOKEN

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler(["help","start"], _help))
    #
    #  Add command handler to set email address and account.
    app.add_handler(CommandHandler("list_email", setting_list_email))
    app.add_handler(CommandHandler("add_email", setting_add_email))
    app.add_handler(CommandHandler("del_email", setting_del_email))
    app.add_handler(MessageHandler(filters.REPLY, handle_reply_send_email))

    
    async def errorHandler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update or not update.message:
            return

        import traceback
        if context.error:
            excStr = '\n'.join(traceback.format_exception(context.error))
            await update.message.reply_text(f'Error processing command: {excStr}')
        else:
            await update.message.reply_text('Error processing command: (unknown error)')
        
    app.add_error_handler(errorHandler)
    
    assert app.job_queue is not None
    job_queue = app.job_queue
    job_queue.run_repeating(periodic_task, interval=POLL_INTERVAL, first=5, name='email-periodic_task')
    job_queue.run_repeating(periodic_task_error_report, interval=ERR_REPORT_INTERVAL, first=8, name='email-periodic_error_report')

    app.add_error_handler(error)

    # Start the Bot
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
