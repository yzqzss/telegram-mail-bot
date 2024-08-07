import logging
import poplib
from .client_base import EmailClientBase
from utils.mail import Email


logger = logging.getLogger(__name__)

class EmailClientPOP3(EmailClientBase):
    def __init__(self, email_account, passwd, pop3_server=None):
        self.email_account = email_account
        self.password = passwd
        self.pop3_server = pop3_server
        if not pop3_server:
            self.pop3_server = 'pop.'+self.email_account.split('@')[-1]
        self.server = self.connect(self)

    @staticmethod
    def connect(self):
        # parse the server's hostname from email account
        
        server = poplib.POP3_SSL(self.pop3_server)
        # display the welcome info received from server,
        # indicating the connection is set up properly
        logger.info(server.getwelcome().decode('utf8'))
        # authenticating
        server.user(self.email_account)
        server.pass_(self.password)
        return server

    def get_mails_list(self):
        _, mails, _ = self.server.list()
        return mails

    def get_mails_count(self):
        mails = self.get_mails_list()
        return len(mails)

    def get_mail_by_index(self, index):
        resp_status, mail_lines, mail_octets = self.server.retr(index)
        return Email(mail_lines)

    def cleanup(self):
        self.server.quit()
    
    def kill(self):
        self.server.close()



if __name__ == '__main__':
    import sys
    useraccount = sys.argv[1]
    password = sys.argv[2]

    client = EmailClientPOP3(useraccount, password)
    num = client.get_mails_count()
    print(num)
    for i in range(1, num):
        print(client.get_mail_by_index(i))