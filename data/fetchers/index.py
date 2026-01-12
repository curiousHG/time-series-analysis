import requests

url = "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%20100"

headers = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "application/json",
    "Referer": "https://www.nseindia.com/",
}

session = requests.Session()
session.get("https://www.nseindia.com", headers=headers)  # important

data = session.get(url, headers=headers).json()
# write data to a json file
import json

with open("nifty_100_data.json", "w") as f:
    json.dump(data, f, indent=4)
# extract the 'data' field which contains the list of stocks
