import datetime
import logging
import os
import re
import socket
import time
from traceback import format_exc
from typing import Type
import dataclasses
import typing
from telegram import Bot, Update
from telegram.constants import MAX_MESSAGE_LENGTH
from telegram.ext import (Updater, CommandHandler, MessageHandler, ConversationHandler, Filters, CallbackContext)
from pysondb import db as pysondb # type: ignore
from utils import EmailClientBase, EmailClientIMAP, EmailClientPOP3
from utils.smtpclient import send_email

updater: Updater = None # type: ignore[assignment]

socket.setdefaulttimeout(10) # avoid imaplib timeout

emailDB = pysondb.getDb("conf/email_accounts.json")

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s:%(lineno)d - %(message)s',
                    # stream=sys.stdout,
                    level=logging.INFO)
logger = logging.getLogger(__name__)


getconf = lambda x: os.environ.get(x) # or dotenv_values().get(x)
bot_token = getconf('TELEGRAM_TOKEN')
if not bot_token:
    raise Exception('TELEGRAM_TOKEN not set in env or .env file')
_owner_chat_id = getconf('OWNER_CHAT_ID')
if not _owner_chat_id:
    raise Exception('OWNER_CHAT_ID not set in env or .env file')
owner_chat_id = int(_owner_chat_id)
_poll_interval = getconf('POLL_INTERVAL')
if not _poll_interval:
    _poll_interval = '60'
poll_interval = int(_poll_interval)
_err_report_interval = getconf('ERR_REPORT_INTERVAL')
if not _err_report_interval:
    _err_report_interval = '3600'
err_report_interval = int(_err_report_interval)

def is_owner(update: Update) -> bool:
    return update.message.chat_id == owner_chat_id

def handle_large_text(text):
    while text:
        if len(text) < MAX_MESSAGE_LENGTH:
            yield text
            text = None
        else:
            out = text[:MAX_MESSAGE_LENGTH]
            yield out
            text = text.lstrip(out)

def error(update: Update, context: CallbackContext) -> None:
    """Log Errors caused by Updates."""
    logger.warning('Update "%s" caused error "%s"', update, context.error)

def start_callback(update: Update, context: CallbackContext) -> None:
    if not is_owner(update):
        return
    msg = "Use /help to get help"
    # print(update)
    update.message.reply_text(msg)

def _help(update: Update, context: CallbackContext) -> None:
    if not is_owner(update):
        return
    """Send a message when the command /help is issued."""
    help_str = """邮箱设置:
/add_email john.doe@example.com password protocol://server[:port] (例：imaps://imap.google.com，pop3s://outlook.office365.com)
/list_email
/del_email john.doe@example.com
/help get help"""
    context.bot.send_message(update.message.chat_id, 
                    # parse_mode=ParseMode.MARKDOWN,
                    text=help_str)

@dataclasses.dataclass
class EmailConf():
    email_addr: str
    email_passwd: str
    server_uri: str
    smtp_server_uri: str | None
    chat_id: int
    inbox_num: int

def setting_list_email(update: Update, context: CallbackContext) -> None:
    if not is_owner(update):
        return
    
    msg = 'Email Account List:\n'
    for email in emailDB.getAll():
        msg += f"    Email: {email['email_addr']}, Password: {email['email_passwd']}, Server: {email['server']}, InboxNum: {email['inbox_num']}\n"
    update.message.reply_text(msg)

def setting_add_email(update: Update, context: CallbackContext) -> None:
    if not is_owner(update):
        return
    if not context.args:
        update.message.reply_text("Invalid command!")
        return
    email_addr = context.args[0]
    email_passwd = context.args[1]
    email_server = context.args[2]
    email_smtp = None
    if len(context.args) > 3:
        email_smtp = context.args[3]
    
    if not email_server.startswith('imap') and not email_server.startswith('pop3'):
        update.message.reply_text(f"invalid server: {email_server}")
        return
    if email_smtp and not email_smtp.startswith('smtp'):
        update.message.reply_text(f"invalid smtp server: {email_smtp}")
        return
    
    emailConf = EmailConf(
        email_addr=email_addr,
        email_passwd=email_passwd,
        server_uri=email_server,
        smtp_server_uri=email_smtp,
        chat_id=update.message.chat_id,
        inbox_num=-1,
    )
    
    logger.info("received setting_email command.")
    with getEmailClient(emailConf) as client:
        inbox_num = client.get_mails_count()
    emailConf.inbox_num = inbox_num
    
    if emailDB.getByQuery({'email_addr': email_addr}):
        update.message.reply_text(f"Email {email_addr} is already configured! Overriding...")
        emailDB.updateByQuery({'email_addr': email_addr},  dataclasses.asdict(emailConf))
    else:
        emailDB.add(dataclasses.asdict(emailConf))
    
    update.message.reply_text("Configure email success!")


def setting_del_email(update: Update, context: CallbackContext) -> None:
    if not is_owner(update):
        return
    if not context.args:
        update.message.reply_text("Invalid command!")
        return
    email_addr = context.args[0]
    
    emails = emailDB.getByQuery({'email_addr': email_addr})
    if not emails:
        update.message.reply_text(f'cannot find email account: {email_addr}')
        return
    assert len(emails) == 1
    pk = emails[0][emailDB.id_fieldname]
    assert emailDB.deleteById(pk)
    update.message.reply_text(f'Successfully deleted email account {email_addr}')

def safeSendText(sender, content):
    for text in handle_large_text(content):
        safeSend(sender, text)

def safeSend(sender, content):
    for i in range(10):
        try:
            sender(content)
            break
        except Exception:
            logger.warning('cannot send tg msg (retry %d)', i, exc_info=True)
        time.sleep(i * 5)

def getEmailConf(email_addr):
    emailConfs = emailDB.getByQuery({'email_addr': email_addr})
    if not emailConfs:
        raise Exception(f'cannot find config for email {email_addr}')
    assert len(emailConfs) == 1
    emailConf = EmailConf(*emailConfs[0])
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
        except Exception:
            logger.info(f'email client for {emailConf.email_addr} is invalid, re-creating...')
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
def periodic_task() -> None:
    # {
    #     'email_addr': email_addr,
    #     'email_passwd': email_passwd,
    #     'server_uri': email_server,
    #     'chat_id': update.message.chat_id,
    # }
    global PERIODIC_TASK_TICK
    logger.info("entered periodic task, tick: %d...", PERIODIC_TASK_TICK)
    PERIODIC_TASK_TICK += 1
    
    # updater.bot.send_message()
    def handler(emailConfDict):
        logger.info("processing periodic task for %s", emailConfDict)
        try:
            if 'id' in emailConfDict:
                emailConfDict.pop('id')
            emailConf = EmailConf(**emailConfDict)
            email_addr = emailConf.email_addr
        except Exception:
            logger.warning('Cannot parse emailConfDict: %s', emailConfDict, exc_info=True)
            return
        try:            
            with getEmailClient(emailConf) as client:
                new_inbox_num = client.get_mails_count()
                if new_inbox_num > emailConf.inbox_num:
                    for idx in range(emailConf.inbox_num + 1, new_inbox_num + 1):
                        try:
                            mail = client.get_mail_by_index(idx)
                        except Exception:
                            logger.warning('cannot retrieve mail %d for %s', idx, emailConf, exc_info=True)
                            break
                        
                        text = f'''New Email [{emailConf.email_addr}-{idx}]\n'''
                        emailbody, emailfiles = mail.format_email()
                        text += emailbody
                        
                        safeSendText(
                            lambda text: updater.bot.send_message(chat_id=emailConf.chat_id, text=text), # type: ignore[has-type]
                            text
                        )
                        for filename, filemime, file_content in emailfiles:
                            if filemime.startswith('image'):
                                safeSend(
                                    lambda text: updater.bot.send_document(chat_id=emailConf.chat_id, document=text, filename=filename), # type: ignore[has-type]
                                    text
                                )
                            else:
                                safeSend(
                                    lambda text: updater.bot.send_photo(chat_id=emailConf.chat_id, photo=file_content, filename=filename), # type: ignore[has-type]
                                    text
                                )
                        emailDB.updateByQuery({'email_addr': email_addr}, {'inbox_num': idx})
        except Exception as e:
            if email_addr not in PERIODIC_TASK_ERRORS:
                PERIODIC_TASK_ERRORS[email_addr] = {}
            exceptionStr = str(e)
            if exceptionStr not in PERIODIC_TASK_ERRORS[email_addr]:
                PERIODIC_TASK_ERRORS[email_addr][exceptionStr] = []
            PERIODIC_TASK_ERRORS[email_addr][exceptionStr].append(format_exc())
            logger.warning('periodic task error in %s', email_addr, exc_info=True)
    
    # for emailConfDict in emailDB.getAll():
    #     handler(emailConfDict)
    
    # TODO: Implement timeout control
    from multiprocessing.pool import ThreadPool
    for _ in ThreadPool(5).imap_unordered(handler, emailDB.getAll()):
        pass

LAST_ERROR_REPORT_TIME: float | None = None
LAST_ERROR_REPORT_TICK = 0
def periodic_task_error_report():
    global PERIODIC_TASK_ERRORS, LAST_ERROR_REPORT_TIME
    if not PERIODIC_TASK_ERRORS:
        logger.info("No errors since last error report, skipping this report!")
        return
    last_errors = PERIODIC_TASK_ERRORS
    logger.info("last errors: %s", last_errors)
    queries = PERIODIC_TASK_TICK - LAST_ERROR_REPORT_TICK
    PERIODIC_TASK_ERRORS = {}
    text = f'''Error Summary during last {queries} queries in duration {str(datetime.timedelta(seconds=err_report_interval))}:\n'''
    for acc, accErrDict in last_errors.items():
        text += f'Acc: {acc}\n'
        for errType, errList in accErrDict.items():
            text += f'    Error: {errType}, triggered {len(errList)} times\n'
    safeSendText(
        lambda text: updater.bot.send_message(chat_id=owner_chat_id, text=text), # type: ignore[has-type]
        text
    )
    LAST_ERROR_REPORT_TIME = time.time()

def handle_reply_send_email(update: Update, context: CallbackContext):
    original_message = update.message.reply_to_message.text
    email, mail_id, from_email = re.findall(r'^.*?\[(.*?)-(\d+)\]\n[\S\s]+?From: .*?(\S+)\n', original_message)[0]
    reply_message = update.message.text
    subject, split, body = reply_message.partition('\n\n')
    if split != '\n\n':
        update.message.reply_text("Don't know the subject of email. Send the email in this form: \n\n(Your subject here)\n\n(Your body)")
        return
    emailConf = getEmailConf(email)
    emailConf.smtp_server_uri
    send_email(
        smtp_server_uri=emailConf.smtp_server_uri, 
        sender_email=emailConf.email_addr, 
        password=emailConf.email_passwd, 
        receiver_email=from_email, 
        subject=subject, body=body)
    update.message.reply_text(f"Successfully sent the email from {email} to {from_email} with subject {subject}")

def main():
    # Create the EventHandler and pass it your bot's token.
    global updater
    updater = Updater(token=bot_token, use_context=True)
    print(bot_token)

    # Get the dispatcher to register handlers
    dp = updater.dispatcher

    # simple start function
    dp.add_handler(CommandHandler("start", start_callback))

    dp.add_handler(CommandHandler("help", _help))
    #
    #  Add command handler to set email address and account.
    dp.add_handler(CommandHandler("list_email", setting_list_email))
    dp.add_handler(CommandHandler("add_email", setting_add_email))
    dp.add_handler(CommandHandler("del_email", setting_del_email))
    dp.add_handler(MessageHandler(Filters.reply, handle_reply_send_email))
    # TODO: implement send mail
    # dp.add_handler(ConversationHandler(
    #     entry_points=[CommandHandler("send_email", handle_start_send_email)],
    #     states={
    #         SELECT_SENDER: [MessageHandler(Filters.regex('^[^@]+@[^@]+\.[^@]+$'), handle_send_email_semder)],
    #         INPUT_RECEIVER: [MessageHandler(Filters.regex('^[^@]+@[^@]+\.[^@]+$'), handle_send_email_receiver)],
    #         BODY: [MessageHandler(Filters.text, handle_send_email_body)],
    #     },
    #     fallbacks=[CommandHandler("cancel", cancel)],
    # ))

    
    def errorHandler(update: Update, context: CallbackContext):
        import traceback
        if context.error:
            excStr = '\n'.join(traceback.format_exception(context.error))
            update.message.reply_text(f'Error processing command: {excStr}')
        else:
            update.message.reply_text('Error processing command: (unknown error)')
        
    dp.add_error_handler(errorHandler)
    
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler()
    scheduler.add_job(periodic_task, 'interval', seconds=poll_interval, id='email-periodic_task', replace_existing=True)
    scheduler.add_job(periodic_task_error_report, 'interval', seconds=err_report_interval, id='email-periodic_error_report', replace_existing=True)
    scheduler.start()

    dp.add_error_handler(error)

    # Start the Bot
    updater.start_polling()

    # Run the bot until you press Ctrl-C or the process receives SIGINT,
    # SIGTERM or SIGABRT. This should be used most of the time, since
    # start_polling() is non-blocking and will stop the bot gracefully.
    updater.idle()


if __name__ == '__main__':
    main()
