import requests
import json
import libraries.logger as logger


class stagerApi:
    def __init__(self, server_url, debug: bool = False) -> None:
        self.log = logger.fileLogger()
        self.log.initialize("StagerApi")
        self.debug = debug
        self.server_url = server_url
        self.headers = {"Accept": "application/json", "Content-Type": "application/json"}

    def login(self, email: str, password: str) -> dict:
        request = requests.post(self.server_url + "sign-in/account", headers=self.headers,
                                json={"email": email, "password": password}).json()
        if 'sessionToken' in request:
            self.headers["Authorization"] = "Bearer " + request["sessionToken"]
            self.log.info(f"Successful POST request to {self.server_url}sign-in/account/current for user `{email}`", self.debug)
            return request
        else:
            self.log.warn(f"Successful POST request to {self.server_url}sign-in/account/current for user `{email}`", cmdout=self.debug)
            self.log.warn("however the user did not provide correct login credentials.", request['message'], self.debug)
            return request

    def currentAccount(self, sessionToken: str) -> dict:
        header = self.headers.copy()
        header["Authorization"] = "Bearer " + sessionToken
        request = requests.post(self.server_url + "sign-in/account/current", headers=header)
        self.log.info(f"Successful POST request to {self.server_url}sign-in/account/current", self.debug)
        return request.json()

    def profile(self, sessionToken: str) -> dict:
        header = self.headers.copy()
        header["Authorization"] = "Bearer " + sessionToken
        request = requests.get(self.server_url + "crew/profile", headers=header)
        self.log.info(f"Successful GET request to {self.server_url}crew/profile", self.debug)
        return request.json()

    def calendarSyncUrl(self, sessionToken: str) -> dict:
        header = self.headers.copy()
        header["Authorization"] = "Bearer " + sessionToken
        request = requests.get(self.server_url + "crew/calendar-sync-url", headers=header)
        self.log.info(f"Successful GET request to {self.server_url}crew/calendar-sync-url", self.debug)
        return request.json()

    def updateProfilePicture(self, sessionToken: str, picture) -> bool:
        header = self.headers.copy()
        header["Authorization"] = "Bearer " + sessionToken
        request = requests.post(self.server_url + "crew/profile/upload-picture", headers=header)
        self.log.info(f"Successful POST request to {self.server_url}crew/profile/upload-picture", self.debug)
        if request.status_code == 200:
            return True
        else:
            return False

    def shiftOverview(self, sessionToken: str) -> dict:
        header = self.headers.copy()
        header["Authorization"] = "Bearer " + sessionToken
        request = requests.get(self.server_url + "crew/shifts/overview", headers=header)
        self.log.info(f"Successful GET request to {self.server_url}crew/shifts/overview", self.debug)
        return request.json()

    def assignedShifts(self, sessionToken: str) -> dict:
        if sessionToken == "test":
            with open(r'test_data/crew/my-shifts.json', encoding='utf-8') as f:
                return json.load(f)
        header = self.headers.copy()
        header["Authorization"] = "Bearer " + sessionToken
        request = requests.get(self.server_url + "crew/my-shifts", headers=header)
        self.log.info(f"Successful GET request to {self.server_url}crew/my-shifts", self.debug)
        return request.json()

    def openShifts(self, sessionToken: str) -> dict:
        if sessionToken == "test":
            with open(r'test_data/crew/open-shifts.json', encoding='utf-8') as f:
                return json.load(f)
        header = self.headers.copy()
        header["Authorization"] = "Bearer " + sessionToken
        request = requests.get(self.server_url + "crew/open-shifts", headers=header)
        self.log.info(f"Successful GET request to {self.server_url}crew/open-shifts")
        return request.json()

    def colleagues(self, sessionToken: str, day=None) -> dict:
        if sessionToken == "test":
            with open(r'test_data/crew/shifts/colleagues.json', encoding='utf-8') as f:
                return json.load(f)
        header = self.headers.copy()
        header["Authorization"] = "Bearer " + sessionToken
        if type(day) is str:
            request = requests.get(self.server_url + "crew/shifts/colleagues", headers=header,
                                   params={'date': day}).json()
        elif type(day) is object:
            request = requests.get(self.server_url + "crew/shifts/colleagues", headers=header,
                                   params={'date': day.date().isoformat()}).json()
        else:
            request = requests.get(self.server_url + "crew/shifts/colleagues", headers=header).json()

        self.log.info(f"Successful GET request to {self.server_url}crew/shifts/colleagues for date {day}")
        return request

    def setAvalability(self, sessionToken: str, day, available: bool) -> bool:
        header = self.headers.copy()
        header["Authorization"] = "Bearer " + sessionToken
        if available:
            available_str = "AVAILABLE"
        else:
            available_str = "UNAVAILABLE"

        if type(day) is str:
            request = requests.post(self.server_url + "crew/shifts/update-availability", headers=header,
                                    json={"date": day, "type": available_str})
        elif type(day) is object:
            request = requests.post(self.server_url + "crew/shifts/update-availability", headers=header,
                                    json={"date": day.date().isoformat(), "type": available_str})
        else:
            raise ValueError

        if request.status_code == 200:
            self.log.info(f"Successful POST request to {self.server_url}crew/shifts/update-availability", self.debug)
            return True
        else:
            print(request.json())
            self.log.warn(f"Failed POST request to {self.server_url}crew/shifts/update-availability",
                          request.status_code, self.debug)
            return False

    def setRequest(self, sessionToken: str, requested: bool, shift_id: int) -> bool:
        header = self.headers.copy()
        header["Authorization"] = "Bearer " + sessionToken
        if requested:
            endpoint = "crew/shifts/request"
        else:
            endpoint = "crew/shifts/unrequest"
        request = requests.post(self.server_url + endpoint, headers=header, json={"shiftIds": shift_id})
        if request.status_code == 200:
            self.log.info(f"Successful POST request to {self.server_url}{endpoint}", self.debug)
            return True
        else:
            self.log.warn(f"Failed POST request to {self.server_url}{endpoint}", request.status_code, self.debug)
            return False
