"""
Einmalig auf DEINEM PC ausfuehren (nicht auf dem Server):

  1) python3 -m pip install google-auth-oauthlib
  2) client_secret.json (aus der Google Cloud Console) in denselben Ordner legen
  3) python3 get_youtube_token.py
  4) Browser oeffnet sich -> mit dem YouTube-Kanal-Konto anmelden -> erlauben
  5) Die erzeugte token.json nach clipfactory/secrets/<profil>/ kopieren.
     client_secret.json brauchst du nur zum spaeteren Neu-Erzeugen des Tokens.
"""
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
with open("token.json", "w") as f:
    f.write(creds.to_json())
print("\nFertig! token.json wurde erstellt. Jetzt token.json nach")
print("clipfactory/secrets/<profil>/ kopieren. client_secret.json nur aufheben.")
