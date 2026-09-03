==========================================================
 FB GROUP JOINER — SETUP GUIDE
==========================================================

WHAT THIS TOOL DOES
-------------------
Automatically joins local USA community groups on Facebook
from your Facebook Page. It searches groups by city, checks
group quality (real activity), answers join questions, and
keeps a log of everything.


ONE-TIME SETUP (do this once)
-----------------------------
1. Install Python from https://www.python.org/downloads/
   IMPORTANT: On the installer's first screen, tick the
   checkbox "Add python.exe to PATH" before clicking Install.

2. Open Command Prompt (press Win key, type "cmd", Enter)
   and run these two commands, one at a time:

       pip install playwright
       playwright install chromium

3. Done! No accounts.py / config.py editing is required —
   you log in directly in the browser window.


HOW TO RUN
----------
1. Double-click  start_account1.bat
2. A control window (dark UI) and a browser window open.
3. In the browser, log in to YOUR Facebook account.
   (Your login is saved on your own PC in a folder called
   "pw_profile" — you only need to log in once.)
4. In the control window:
   - Paste your Facebook PAGE link (the page you want to
     join groups as), e.g.
     https://www.facebook.com/profile.php?id=XXXXXXXXXXX
   - Enter the city/state you want to target.
   - Click START.

Running more accounts: use start_account2.bat,
start_account3.bat ... start_account10.bat — each one uses
its own separate browser profile and its own log files, so
they never mix.


USEFUL EXTRAS
-------------
- logout_account.py : logs a profile out of Facebook.
  Example:   py logout_account.py 2
  (logs out account 2's browser profile)

- groups_log_N.csv / joined_groups_N.txt : created
  automatically while running — your history of joined /
  skipped groups.


SAFETY NOTES
------------
- The tool uses human-like random delays. Do NOT edit the
  delay settings to make it faster — Facebook may restrict
  the account.
- Recommended: run 2-3 accounts at a time, not all 10 at
  once (RAM + same-IP suspicion).
- NEVER share your pw_profile folders with anyone — they
  contain your Facebook login session.
