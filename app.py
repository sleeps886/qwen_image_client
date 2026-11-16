"""FastAPI web launcher. Legacy desktop UI remains in desktop_app.py."""
import uvicorn
import logging
from web import app


class QuietLogPolling(logging.Filter):
    def filter(self,record):
        args=record.args
        return not (isinstance(args,tuple) and len(args)>=5 and args[2]=='GET'
            and str(args[3]).startswith('/api/logs/') and str(args[4])=='200')


if __name__ == '__main__':
    logging.getLogger('uvicorn.access').addFilter(QuietLogPolling())
    uvicorn.run(app, host='127.0.0.1', port=8870)
