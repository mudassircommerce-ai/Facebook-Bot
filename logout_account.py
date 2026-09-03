# ============================================================
# Facebook Logout Utility
# Kisi account ke browser profile se Facebook login hatata hai
# Chalane ka tarika:  py logout_account.py 1   (account 1)
#                     py logout_account.py 3   (account 3)
# ============================================================
import os
import sys
import asyncio
from playwright.async_api import async_playwright

INSTANCE = sys.argv[1] if len(sys.argv) > 1 else "1"
SUFFIX   = "" if INSTANCE == "1" else f"_{INSTANCE}"

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
PW_PROFILE_DIR = os.path.join(APP_DIR, f"pw_profile{SUFFIX}")


async def main():
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=PW_PROFILE_DIR,
            headless=True,
            args=["--disable-notifications"],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        # Facebook khol ke storage (localStorage etc.) saaf karo
        try:
            await page.goto("https://www.facebook.com/", timeout=30000,
                            wait_until="domcontentloaded")
            await page.evaluate(
                "() => { try { localStorage.clear(); sessionStorage.clear(); } catch(e) {} }"
            )
        except Exception as e:
            print(f"[Account {INSTANCE}] Facebook load warning: {e}")

        # Saare cookies delete — yehi asal logout hai
        await ctx.clear_cookies()

        # Verify: dobara facebook kholo, login page aana chahiye
        try:
            await page.goto("https://www.facebook.com/", timeout=30000,
                            wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            html = await page.content()
            logged_out = ("login" in page.url.lower()
                          or 'name="email"' in html or 'name="pass"' in html)
            if logged_out:
                print(f"[Account {INSTANCE}] LOGOUT OK — login page aa raha hai")
            else:
                print(f"[Account {INSTANCE}] WARNING — abhi bhi logged in lag raha hai ({page.url})")
        except Exception as e:
            print(f"[Account {INSTANCE}] Verify warning: {e}")

        await ctx.close()


asyncio.run(main())
