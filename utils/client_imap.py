import logging
import imaplib
from urllib.parse import ParseResult, urlparse

from .client_base import EmailClientBase
from .mail import Email

logger = logging.getLogger(__name__)

class EmailClientIMAP(EmailClientBase):
    def __init__(self, email_account, passwd, server_uri=None):
        self.email_account = email_account
        self.password = passwd
        if not server_uri:
            server_uri = 'imaps://imap.'+self.email_account.split('@')[-1]
        self.server_uri: ParseResult = urlparse(server_uri) # type: ignore
        self.server = self.connect()

    def connect(self):
        if self.server_uri.scheme == 'imaps':
            server = imaplib.IMAP4_SSL(self.server_uri.hostname, self.server_uri.port or imaplib.IMAP4_SSL_PORT)
        else:
            raise RuntimeError(f'Unsupported IMAP protocol variant: {self.server_uri.scheme}')
        
        # display the welcome info received from server,
        # indicating the connection is set up properly
        logger.info('imap server welcome: %s', server.welcome.decode('utf8'))
        # authenticating
        status, statusText = server.login(self.email_account, self.password)
        assert status == 'OK', f'imap failed to login: {status}'
        logger.info('imap login ok: %s', statusText)
        return server

    def get_mails_count(self):
        # Select the mailbox you want to check
        status, inboxdata = self.server.select("inbox", readonly=True)
        assert status == 'OK', f'imap failed to select: {status}'
        return int(inboxdata[0].decode())

    def get_mail_by_index(self, index):
        status, data = self.server.fetch('%d' % index, '(RFC822)')
        assert status == 'OK', f'imap failed to fetch: {status}'
        mail_lines = data[0][1].decode()
        return Email(mail_lines)

    def refresh_connection(self):
        self.server.noop()
    
    def cleanup(self):
        self.server.logout()
    
    def kill(self):
        self.server.close()

if __name__ == '__main__':
    import sys
    useraccount = sys.argv[1]
    password = sys.argv[2]

    client = EmailClientIMAP(useraccount, password)
    num = client.get_mails_count()
    print(num)
    for i in range(1, num):
        print(client.get_mail_by_index(i))