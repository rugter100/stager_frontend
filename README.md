# Stager frontend tool

Webpanel that acts as a 'man in the middle' between the user and stager. Made because i didnt like dealing with the stager app since its slow and buggy in my own experience.

This tool is specifically made for the stager of Neushoorn (https://neushoorn.nl) but can be adjusted for your use case
Apart from retrieving data from stager using your session token it also retrieves data from https://neushoorn.nl to view specific publically accessible showtimes

## Current Capabilities

 - Show upcoming (planned) shifts
   - Start and end time
   - total shift duration
   - production manager for the shift
 - Show upcoming open shifts
   - show anmount of shifts avalible
   - adjust avalability
   - show if you are already planned for a shift
 - Details for planned and upcoming shifts
   - Show details and other shows of that date
   - (if planned shift) colleagues for that shift
 - Past shifts
   - Show worked shifts and view details

(The past shifts functionality is unable to show shifts before the first use of the user of this panel due to stager not having a way to view past shifts)

## DISCLAIMER

This tool is not endorsed, supported or maintained by Stager B.V., Stichting Popcultuur Neushoorn or Neushoorn B.V. This is a hobby project made and maintained by Vamting and all issues, bugs and concerns should be reported via THIS github repo. 

is a simple tool giving users the ability to view their shifts, upcoming shifts and adjust avalability for the neushoorn stager. Additionally to this it gathers data from other related sources to share more details and information about upcoming shifts. This website does this by acting as a man in the middle between stager and the end user. It has been done this way due to stager not supporting a Single Sign On (SSO) feature for this functionality. This does mean this tool handles the users username and password, however it only saves the username and does not store the user's stager password.Please be aware that this website does not follow intended ways to do this due to lack of SSO. Please be aware that this tool is a hobby project not designed for public use and should not be used as a definitive source of data from Stager or Neushoorn.
