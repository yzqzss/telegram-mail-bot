from .client_base import EmailClientBase
from .client_imap import EmailClientIMAP
from .client_pop3 import EmailClientPOP3

from dotenv import dotenv_values
import os
os.environ.update({k:v for k,v in dotenv_values().items() if v})