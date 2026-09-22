import json
import urllib.request

TOKEN = "8629756462:AAHSn66-SVOZzWp_UrBj36bHjF1hpts5bco"
url = f"https://api.telegram.org/bot{TOKEN}/getMe"

try:
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        print(f"Bot bilgisi: {data}")
except Exception as e:
    print(f"Hata: {e}")
