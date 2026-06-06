# ver 1.0.0
# Python3.12
import json
import requests
import libraries.logger as logger

class _cache(object):

    def __init__(self):
        self.data = {}

    def __setitem__(self, keys, item):
        self.data[keys] = [item]

    def __getitem__(self, keys):
        return self.data[keys][0]

    def getcount(self, keys):
        return self.data[keys][0]

    def listkeys(self):
        return list(self.data)

class send:

    def __init__(self):
        self.topic = None
        self.cache = _cache()
        self.log = logger.fileLogger()
        self.log.initialize('ntfy')
        self.log.info("Logging Initialized!")
        with open(r'config.json') as f:
            self.cfg = json.load(f)

    def post(self, topic=False, data="No Data", headers={}):
        if topic:
            self.topic = topic
        requests.post(f"{self.cfg['presetup']['ntfy']['domain']}/{self.topic}",
                      data=data.encode(encoding='utf-8'),
                      headers=headers)
        self.log.info(f"Successfully sent notification to topic: {self.topic} with data: {data} and headers: {headers}", False)
        # Header documentation https://docs.ntfy.sh/publish/
        # Valid headers:
        # Title - Title of notification
        # priority - prio of the notification: 1-5 low-high, 3 is default
        # Tags - basically emojis
        # Click - URL to go to when the notification is clicked
        # Attach - an image file to attach to the notification
        # Actions - Actions below the notificatin
        # Email - adds an email i guess?
        # At - schedules a notification
