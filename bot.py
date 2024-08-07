import email
import logging
import os
import socket
import sys
import time
from typing import Type
import dataclasses
from telegram import Bot, Update
from telegram.constants import MAX_MESSAGE_LENGTH
from telegram.ext import (Updater, CommandHandler, CallbackContext)
from pysondb import db as pysondb # type: ignore
from utils import EmailClientIMAP, EmailClientPOP3
from dotenv import dotenv_values

updater: Updater = None # type: ignore[assignment]

socket.setdefaulttimeout(10) # avoid imaplib timeout

emailDB = pysondb.getDb("conf/email_accounts.json")

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s:%(lineno)d - %(message)s',
                    # stream=sys.stdout,
                    level=logging.INFO)
logger = logging.getLogger(__name__)


getconf = lambda x: os.environ.get(x) or dotenv_values().get(x)
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
/add_email john.doe@example.com password
/inbox
/get mail_index
/help get help"""
    # help_str = "*Mailbox Setting*:\n" \
    #            "/setting john.doe@example.com password\n" \
    #            "/inbox\n" \
    #            "/get mail_index"
    context.bot.send_message(update.message.chat_id, 
                    # parse_mode=ParseMode.MARKDOWN,
                    text=help_str)

@dataclasses.dataclass
class EmailConf():
    email_addr: str
    email_passwd: str
    protocol: str
    server: str
    chat_id: int
    inbox_num: int

def setting_list_email(update: Update, context: CallbackContext) -> None:
    if not is_owner(update):
        return
    
    msg = 'Email Account List:\n'
    for email in emailDB.getAll():
        msg += f"    Email: {email['email_addr']}, Password: {email['email_passwd']}, Protocol: {email['protocol']}, Server: {email['server']}, InboxNum: {email['inbox_num']}\n"
    update.message.reply_text(msg)

def setting_add_email(update: Update, context: CallbackContext) -> None:
    if not is_owner(update):
        return
    if not context.args:
        update.message.reply_text("Invalid command!")
        return
    email_addr = context.args[0]
    email_passwd = context.args[1]
    email_protocol = context.args[2]
    email_server = context.args[3]
    
    if email_protocol not in ('IMAP', 'POP3'):
        update.message.reply_text(f"invalid protocol: {email_protocol}")
        return
    
    emailConf = EmailConf(
        email_addr=email_addr,
        email_passwd=email_passwd,
        protocol=email_protocol,
        server=email_server,
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
    # context.job_queue.run_repeating(periodic_task, interval=60, context=update.message.chat_id)
    # context.chat_data['job'] = job
    # logger.info("periodic task scheduled.")


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
        for i in range(10):
            try:
                sender(text)
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

emailClientCache: dict[tuple, EmailClientIMAP | EmailClientPOP3] = {}
def getEmailClient(emailConf: EmailConf) -> EmailClientIMAP | EmailClientPOP3:
    cacheKey = tuple(dataclasses.astuple(emailConf))
    emailClient: EmailClientIMAP | EmailClientPOP3 | None = emailClientCache.get(cacheKey, None)
    if emailClient:
        try:
            emailClient.server.noop()
            logger.info(f'email client for {emailConf.email_addr} is still good')
            return emailClient
        except Exception:
            logger.info(f'email client for {emailConf.email_addr} is invalid, re-creating...')
            emailClient = None
    EmailClient: Type[EmailClientPOP3] | Type[EmailClientIMAP]
    if emailConf.protocol == 'POP3':
        EmailClient = EmailClientPOP3
    elif emailConf.protocol == 'IMAP':
        EmailClient = EmailClientIMAP
    else:
        raise Exception(f"invalid email protocol: {emailConf.protocol}")
    
    emailClient = EmailClient(emailConf.email_addr, emailConf.email_passwd, emailConf.server)
    emailClientCache[cacheKey] = emailClient
    return emailClient

def periodic_task() -> None:
    # {
    #     'email_addr': email_addr,
    #     'email_passwd': email_passwd,
    #     'protocol': email_protocol,
    #     'server': email_server,
    #     'chat_id': update.message.chat_id,
    # }
    
    logger.info("entered periodic task...")
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
                            continue
                        
                        safeSendText(
                            lambda text: updater.bot.send_message(chat_id=emailConf.chat_id, text=text), # type: ignore[has-type]
                            mail.__repr__()
                        )
                        emailDB.updateByQuery({'email_addr': email_addr}, {'inbox_num': idx})
        except Exception:
            logger.warning('periodic task error in %s', email_addr, exc_info=True)
    
    # for emailConfDict in emailDB.getAll():
    #     handler(emailConfDict)
    from multiprocessing.pool import ThreadPool
    for _ in ThreadPool(5).imap_unordered(handler, emailDB.getAll()):
        pass

# def inbox(update: Update, context: CallbackContext) -> None:
#     if not is_owner(update):
#         return
#     logger.info("received inbox command.")
#     if not context.args:
#         update.message.reply_text("need to supply email_addr")
#         return
#     email_addr = context.args[0]
#     emailConf = getEmailConf(email_addr)
#     with getEmailClient(emailConf) as client:
#         new_num = client.get_mails_count()
#         reply_text = "The index of newest mail is *%d*," \
#                     " received *%d* new mails since last" \
#                     " time you checked." % \
#                     (new_num, new_num - )
#         inbox_num = new_num
#         update.message.reply_text("need to supply email_addr")

# def get_email(update: Update, context: CallbackContext) -> None:
#     if not is_owner(update):
#         return
#     if not context.args:
#         update.message.reply_text('no email id supplied')
#         return
#     index = context.args[0]
#     logger.info("received get command.")
#     with EmailClient(email_addr, email_passwd) as client:
#         mail = client.get_mail_by_index(index)
#         content = mail.__repr__()
#         for text in handle_large_text(content):
#             context.bot.send_message(update.message.chat_id,
#                              text=text)

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
    
    def errorHandler(update: Update, context: CallbackContext):
        import traceback
        if context.error:
            update.message.reply_text(f'Error processing command: {traceback.format_exception(context.error)}')
        else:
            update.message.reply_text('Error processing command: (unknown error)')
        
    dp.add_error_handler(errorHandler)
    
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler()
    scheduler.add_job(periodic_task, 'interval', seconds=poll_interval, id='email-periodic_task', replace_existing=True)
    scheduler.start()

    # dp.add_handler(CommandHandler("inbox", inbox))

    # dp.add_handler(CommandHandler("get", get_email))


    dp.add_error_handler(error)

    # Start the Bot
    updater.start_polling()

    # Run the bot until you press Ctrl-C or the process receives SIGINT,
    # SIGTERM or SIGABRT. This should be used most of the time, since
    # start_polling() is non-blocking and will stop the bot gracefully.
    updater.idle()


if __name__ == '__main__':
    main()
