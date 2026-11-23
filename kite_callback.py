"""Receive a Zerodha Kite Connect login callback on this computer.

This script exchanges a successful login request token for an access token.
It does not place trades or orders. Keep the generated access token private.
"""

import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv, set_key
from kiteconnect import KiteConnect


HOST = "127.0.0.1"
PORT = 8000
ENV_FILE = Path(__file__).with_name(".env")


def save_access_token(access_token):
    """Add or update only KITE_ACCESS_TOKEN in the local .env file."""
    set_key(ENV_FILE, "KITE_ACCESS_TOKEN", access_token)


class CallbackHandler(BaseHTTPRequestHandler):
    """Handle a single callback without logging its sensitive URL."""

    def do_GET(self):
        request = urlparse(self.path)
        if request.path != "/callback":
            self.send_message(404, "This is not the Kite callback address.")
            return

        parameters = parse_qs(request.query)
        status = parameters.get("status", [""])[0]
        request_token = parameters.get("request_token", [""])[0]

        if status != "success" or not request_token:
            self.send_message(400, "Zerodha login failed. Run the script again to retry.")
            self.report_result(False)
            return

        try:
            session = self.server.kite.generate_session(
                request_token, api_secret=self.server.api_secret
            )
            access_token = session.get("access_token")
            if not access_token:
                raise ValueError("The login response did not contain an access token.")

        except Exception:
            # Exception details can contain sensitive data.
            self.send_message(502, "Kite token exchange failed. Run the script again to retry.")
            self.report_result(False)
            return

        try:
            save_access_token(access_token)
        except Exception:
            self.send_message(500, "Could not save the access token. Check that .env is writable.")
            self.report_result(False)
            return

        self.send_message(200, "Login successful. You can close this browser tab.")
        self.report_result(True)

    def send_message(self, status_code, message):
        page = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>Kite login</title></head><body>"
            f"<p>{message}</p></body></html>"
        ).encode("utf-8")
        try:
            self.send_response(status_code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(page)
        except ConnectionError:
            # The browser may close the connection before the reply is sent.
            pass

    def report_result(self, succeeded):
        if succeeded:
            print("Login successful. The access token was saved in .env.")
        else:
            print("Login failed. No access token was saved.")

        # Stop the server after this callback has been handled.
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def log_message(self, format_string, *args):
        # The default server log includes the URL, which can contain a token.
        pass


def main():
    try:
        load_dotenv(dotenv_path=ENV_FILE)
    except (OSError, UnicodeError):
        print("Login setup failed. Check that .env is readable and uses UTF-8.")
        return
    api_key = os.getenv("KITE_API_KEY")
    api_secret = os.getenv("KITE_API_SECRET")

    if not api_key or not api_secret:
        print("Login setup failed. Add KITE_API_KEY and KITE_API_SECRET to .env.")
        return

    try:
        server = HTTPServer((HOST, PORT), CallbackHandler)
    except OSError:
        print("Could not start the local server. Check whether port 8000 is already in use.")
        return

    try:
        kite = KiteConnect(api_key=api_key)
        server.kite = kite
        server.api_secret = api_secret

        try:
            opened = webbrowser.open(kite.login_url())
        except Exception:
            opened = False

        if not opened:
            print("Could not open the Zerodha login page in your default browser.")
            return

        print(f"Waiting for the Zerodha login callback at http://{HOST}:{PORT}/callback ...")
        server.serve_forever()
    except KeyboardInterrupt:
        print("Login was cancelled. No access token was saved.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
