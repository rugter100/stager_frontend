import requests
import json
from datetime import datetime


class stagerApi:
    def __init__(self, server_url) -> dict:
        self.server_url = server_url
        self.headers = {"Accept": "application/json", "Content-Type": "application/json"}

    def login(self, email: str, password: str) -> dict:
        request = requests.post(self.server_url + "sign-in/account", headers=self.headers,
                                json={"email": email, "password": password}).json()
        if 'sessionToken' in request:
            self.headers["Authorization"] = "Bearer " + request["sessionToken"]
            return request
        else:
            return request

    def currentAccount(self, sessionToken: str) -> dict:
        header = self.headers.copy()
        header["Authorization"] = "Bearer " + sessionToken
        request = requests.post(self.server_url + "sign-in/account/current", headers=header)
        return request.json()

    def profile(self, sessionToken: str) -> dict:
        header = self.headers.copy()
        header["Authorization"] = "Bearer " + sessionToken
        request = requests.get(self.server_url + "crew/profile", headers=self.headers)
        return request.json()

    def calendarSyncUrl(self, sessionToken: str) -> dict:
        header = self.headers.copy()
        header["Authorization"] = "Bearer " + sessionToken
        request = requests.get(self.server_url + "crew/calendar-sync-url", headers=self.headers)
        return request.json()

    def updateProfilePicture(self, sessionToken: str, picture) -> bool:
        header = self.headers.copy()
        header["Authorization"] = "Bearer " + sessionToken
        request = requests.post(self.server_url + "crew/profile/upload-picture", headers=self.headers)
        if request.status_code == 200:
            return True
        else:
            return False

    def shiftOverview(self, sessionToken: str) -> dict:
        header = self.headers.copy()
        header["Authorization"] = "Bearer " + sessionToken
        request = requests.get(self.server_url + "crew/shifts/overview", headers=self.headers)
        return request.json()

    def assignedShifts(self, sessionToken: str) -> dict:
        if sessionToken == "test":
            with open(r'test_data/crew/my-shifts.json', encoding='utf-8') as f:
                return json.load(f)
        header = self.headers.copy()
        header["Authorization"] = "Bearer " + sessionToken
        request = requests.get(self.server_url + "crew/my-shifts", headers=self.headers)
        return request.json()

    def openShifts(self, sessionToken: str) -> dict:
        header = self.headers.copy()
        header["Authorization"] = "Bearer " + sessionToken
        request = requests.get(self.server_url + "crew/open-shifts", headers=self.headers)
        return request.json()

    def colleagues(self, sessionToken: str, day=None) -> dict:
        if sessionToken == "test":
            with open(r'test_data/crew/shifts/colleagues.json', encoding='utf-8') as f:
                return json.load(f)
        header = self.headers.copy()
        header["Authorization"] = "Bearer " + sessionToken
        if type(day) is str:
            request = requests.get(self.server_url + "crew/shifts/colleagues", headers=self.headers,
                                   params={'date': day}).json()
        elif type(day) is object:
            request = requests.get(self.server_url + "crew/shifts/colleagues", headers=self.headers,
                                   params={'date': day.date().isoformat()}).json()
        else:
            request = requests.get(self.server_url + "crew/shifts/colleagues", headers=self.headers).json()

        return request

    def setAvalability(self, sessionToken: str, day, available: bool) -> bool:
        header = self.headers.copy()
        header["Authorization"] = "Bearer " + sessionToken
        if available:
            available_str = "AVAILABLE"
        else:
            available_str = "UNAVAILABLE"

        if type(day) is str:
            request = requests.post(self.server_url + "crew/shifts/update-availability", headers=self.headers,
                                    json={"date": day, "available": available_str})
        elif type(day) is object:
            request = requests.post(self.server_url + "crew/shifts/update-availability", headers=self.headers,
                                    json={"date": day.date().isoformat(), "available": available_str})
        else:
            raise ValueError

        if request.status_code == 200:
            return True
        else:
            return False

    def setRequest(self, sessionToken: str, requested: bool, shift_id: int) -> bool:
        header = self.headers.copy()
        header["Authorization"] = "Bearer " + sessionToken
        if requested:
            endpoint = "crew/shifts/request"
        else:
            endpoint = "crew/shifts/unrequest"
        request = requests.post(self.server_url + endpoint, headers=self.headers, json={"shiftIds": shift_id})
        if request.status_code == 200:
            return True
        else:
            return False

