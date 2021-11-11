"""Python module for accessing SkyQ box and EPG, and sending commands."""
import importlib
import logging
import time
from datetime import datetime

import pycountry

from .classes.app import AppInformation
from .classes.channel import ChannelInformation
from .classes.channelepg import ChannelEPGInformation
from .classes.device import DeviceInformation
from .classes.deviceaccess import DeviceAccess
from .classes.favourite import FavouriteInformation
from .classes.media import MediaInformation
from .classes.programme import Programme
from .classes.recordings import RecordingsInformation
from .const import (COMMANDS, CURRENT_TRANSPORT_STATE, EPG_ERROR_NO_DATA,
                    EPG_ERROR_PAST_END, SKY_STATE_NOMEDIA, SKY_STATE_OFF,
                    SKY_STATE_ON, SKY_STATE_PAUSED, SKY_STATE_PLAYING,
                    SKY_STATE_STANDBY, SKY_STATE_STOPPED,
                    SKY_STATE_TRANSITIONING)

_LOGGER = logging.getLogger(__name__)


class SkyQRemote:
    """SkyQRemote is the instantiation of the SKYQ remote ccontrol."""

    commands = COMMANDS

    def __init__(self, host, epgCacheLen=20, port=49160, jsonPort=9006):
        """Stand up a new SkyQ box."""
        self.deviceSetup = False
        self._host = host
        self._remoteCountry = None
        self._overrideCountry = None
        self._epgCountryCode = None
        self._test_channel = None
        self._port = port
        self._jsonport = jsonPort
        self._soapControlURL = None
        self._channel = None
        self._programme = None
        self._recordedProgramme = None
        self._lastProgrammeEpg = None
        self._lastPvrId = None
        self._error = False
        self._deviceAccess = DeviceAccess(self._host, self._jsonport)
        self._epgCacheLen = epgCacheLen
        self._channellist = None

        self._appInformation = None
        self._channelInformation = None
        self._deviceInformation = None
        self._channelEPGInformation = None
        self._favouriteInformation = None
        self._mediaInformation = None
        self._recordingsInformation = None

        deviceInfo = self.getDeviceInformation()
        if not deviceInfo:
            return None

        self._setupDevice()

    def powerStatus(self) -> str:
        """Get the power status of the Sky Q box."""
        if not self._remoteCountry:
            self._setupRemote()

        if self._soapControlURL is None:
            return SKY_STATE_OFF

        systemInfo = self._deviceInformation.getSystemInformation()

        if systemInfo is None:
            return SKY_STATE_OFF
        if "activeStandby" in systemInfo and systemInfo["activeStandby"] is True:
            return SKY_STATE_STANDBY

        return SKY_STATE_ON

    def getCurrentState(self):
        """Get current state of the SkyQ box."""
        if not self._remoteCountry:
            self._setupRemote()

        if self._soapControlURL is None:
            return SKY_STATE_OFF

        response = self._deviceInformation.getTransportInformation(self._soapControlURL)
        if response is not None:
            state = response[CURRENT_TRANSPORT_STATE]
            if state in [SKY_STATE_NOMEDIA, SKY_STATE_STOPPED]:
                return SKY_STATE_STANDBY
            if state in [SKY_STATE_PLAYING, SKY_STATE_TRANSITIONING]:
                return SKY_STATE_PLAYING
            if state == SKY_STATE_PAUSED:
                return SKY_STATE_PAUSED
        return SKY_STATE_OFF

    def getActiveApplication(self):
        """Get the active application on Sky Q box."""
        if not self._appInformation:
            self._appInformation = AppInformation(self._deviceAccess)

        return self._appInformation.getActiveApplication()

    def getCurrentMedia(self):
        """Get the currently playing media on the SkyQ box."""
        if not self._mediaInformation:
            self._mediaInformation = MediaInformation(
                self._deviceAccess, self._soapControlURL, self._remoteCountry, self._test_channel
            )

        return self._mediaInformation.getCurrentMedia()

    def getEpgData(self, sid, epgDate, days=2):
        """Get EPG data for the specified channel/date."""
        if not self._channelEPGInformation:
            self._channeEPGInformation = ChannelEPGInformation(
                self._deviceAccess, self._remoteCountry, self._test_channel, self._epgCacheLen
            )

        return self._channeEPGInformation.getEpgData(sid, epgDate, days)

    def getProgrammeFromEpg(self, sid, epgDate, queryDate):
        """Get programme from EPG for specfied time and channel."""
        sidint = 0
        try:
            sidint = int(sid)
        except ValueError:
            if not self._error:
                self._error = True
                _LOGGER.info(
                    f"I0010 - Programme data not found for host: {self._host}/{self._overrideCountry} sid: {sid} : {epgDate}"
                )
                return EPG_ERROR_NO_DATA

            self._error = False

        programmeEpg = f"{str(sidint)} {epgDate.strftime('%Y%m%d')}"
        if self._lastProgrammeEpg == programmeEpg and queryDate < self._programme.endtime:
            return self._programme

        epgData = self.getEpgData(sidint, epgDate)

        if len(epgData.programmes) == 0:
            if not self._error:
                self._error = True
                _LOGGER.info(
                    f"I0020 - Programme data not found for host: {self._host}/{self._overrideCountry} sid: {sid} : {epgDate}"
                )
            return EPG_ERROR_NO_DATA

        self._error = False

        try:
            programme = next(p for p in epgData.programmes if p.starttime <= queryDate and p.endtime >= queryDate)

            self._programme = programme
            self._lastProgrammeEpg = programmeEpg
            return programme

        except StopIteration:
            return EPG_ERROR_PAST_END

    def getCurrentLiveTVProgramme(self, sid):
        """Get current live programme on the specified channel."""
        try:
            queryDate = datetime.utcnow()
            programme = self.getProgrammeFromEpg(sid, queryDate, queryDate)
            if not isinstance(programme, Programme):
                return None

            return programme
        except Exception as err:
            _LOGGER.exception(f"X0010 - Error occurred: {self._host} : {sid} : {err}")
            return None

    def getRecordings(self, status=None):
        """Get the list of available Recordings."""
        if not self._recordingsInformation:
            self._recordingsInformation = RecordingsInformation(self._deviceAccess, self._remoteCountry)

        return self._recordingsInformation.getRecordings()

    def getRecording(self, pvrId):
        """Get the recording details."""
        if not self._recordingsInformation:
            self._recordingsInformation = RecordingsInformation(self._deviceAccess, self._remoteCountry)

        if self._lastPvrId == pvrId:
            return self._recordedProgramme
        self._lastPvrId = pvrId

        self._recordedProgramme = self._recordingsInformation.getRecording(pvrId)
        return self._recordedProgramme

    def getDeviceInformation(self):
        """Get the device information from the SkyQ box."""
        if not self._deviceInformation:
            self._deviceInformation = DeviceInformation(self._deviceAccess)

        deviceInfo = self._deviceInformation.getDeviceInformation(self._overrideCountry)
        self._epgCountryCode = deviceInfo.epgCountryCode
        return deviceInfo

    def getChannelList(self):
        """Get Channel list for Sky Q box."""
        if not self._channelInformation:
            self._channelInformation = ChannelInformation(self._deviceAccess, self._remoteCountry, self._test_channel)

        return self._channelInformation.getChannelList()

    def getChannelInfo(self, channelNo):
        """Retrieve channel information for specified channelNo."""
        if not self._channelInformation:
            self._channelInformation = ChannelInformation(self._deviceAccess, self._remoteCountry, self._test_channel)

        return self._channelInformation.getChannelInfo(channelNo)

    def getFavouriteList(self):
        """Retrieve the list of favourites."""
        if not self._favouriteInformation:
            self._favouriteInformation = FavouriteInformation(self._deviceAccess)

        if not self._channellist:
            self.getChannelList()
        return self._favouriteInformation.getFavouriteList(self._channellist)

    def press(self, sequence):
        """Issue the specified sequence of commands to SkyQ box."""
        if isinstance(sequence, list):
            for item in sequence:
                if item.casefold() not in self.commands:
                    _LOGGER.error(f"E0020 - Invalid command: {self._host} : {item}")
                    break
                self._deviceAccess.sendCommand(self._port, self.commands[item.casefold()])
                time.sleep(0.5)
        elif sequence not in self.commands:
            _LOGGER.error(f"E0030 - Invalid command: {self._host} : {sequence}")
        else:
            self._deviceAccess.sendCommand(self._port, self.commands[sequence.casefold()])

    def setOverrides(self, overrideCountry=None, test_channel=None, jsonPort=None, port=None):
        """Override various items."""
        if overrideCountry:
            self._overrideCountry = overrideCountry
        if test_channel:
            self._test_channel = test_channel
        if jsonPort:
            self._jsonport = jsonPort
        if port:
            self.port = port

    def _setupRemote(self):
        deviceInfo = self.getDeviceInformation()
        if not deviceInfo:
            return

        if not self.deviceSetup:
            self._setupDevice()

        if not self._remoteCountry and self.deviceSetup:
            SkyQCountry = self._importCountry(self._epgCountryCode)
            self._remoteCountry = SkyQCountry()

    def _setupDevice(self):

        url_index = 0
        self._soapControlURL = None
        while self._soapControlURL is None and url_index < 50:
            self._soapControlURL = self._deviceAccess.getSoapControlURL(url_index)["url"]
            url_index += 1

        self.deviceSetup = True

    def _importCountry(self, countryCode):
        """Work out the country for the Country Code."""
        try:
            country = pycountry.countries.get(alpha_3=countryCode).alpha_2.casefold()
            SkyQCountry = importlib.import_module("pyskyqremote.country.remote_" + country).SkyQCountry

        except (AttributeError, ModuleNotFoundError) as err:
            _LOGGER.warning(f"W0010 - Invalid country, defaulting to GBR: {self._host} : {countryCode} : {err}")

            from pyskyqremote.country.remote_gb import SkyQCountry

        return SkyQCountry
