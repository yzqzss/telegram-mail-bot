import logging

logger = logging.getLogger(__name__)

class EmailClientBase(object):
    def __init__(self, email_account, passwd, server_addr=None):
        raise NotImplementedError()
    
    @staticmethod
    def connect(self):
        raise NotImplementedError()
    
    def get_mails_count(self):
        raise NotImplementedError()
    
    def get_mail_by_index(self, index):
        raise NotImplementedError()
    
    def cleanup(self):
        raise NotImplementedError()
    
    def kill(self):
        raise NotImplementedError()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        return False
    
    # def __enter__(self):
    #     return self

    # def __exit__(self, exc_type, exc_val, exc_tb):
    #     if exc_type is None:
    #         logger.info('exited normally\n')
    #         self.cleanup()
    #     else:
    #         logger.error('raise an exception! ' + str(exc_type))
    #         self.server.close()
    #         return False # Propagate
