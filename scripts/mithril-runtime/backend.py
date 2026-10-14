"""A session-owned loopback Agent backend with a localhost-published TCP bridge."""
import os
import hmac
import re
import urllib.parse
import selectors
import signal
import socket
import subprocess
import threading
import time

child = None
stopping = threading.Event()
session_token = ''

def authorized_header(raw, token):
    try:
        lines = raw.decode('iso-8859-1').split('\r\n')
        request = lines[0].split(' ')
        if len(request) != 3 or request[2] != 'HTTP/1.1': return False
        headers = [line.split(':', 1) for line in lines[1:] if line]
        if any(len(pair) != 2 for pair in headers): return False
        values = [value.strip() for key, value in headers if key.lower() == 'x-hermes-session-token']
        path = urllib.parse.urlsplit(request[1])
        if not values and path.path == '/api/ws': values = urllib.parse.parse_qs(path.query).get('token', [])
        return len(values) == 1 and bool(re.fullmatch(r'[A-Za-z0-9_-]{32,128}', values[0])) and hmac.compare_digest(values[0], token)
    except (ValueError, UnicodeError): return False

def forward(client):
    upstream = None
    try:
        client.settimeout(10)
        first = b''
        while b'\r\n\r\n' not in first and len(first) <= 32768:
            chunk = client.recv(4096)
            if not chunk: return
            first += chunk
        header = first.split(b'\r\n\r\n', 1)[0]
        if len(header) > 32768 or not authorized_header(header, session_token):
            client.sendall(b'HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            return
        upstream = socket.create_connection(('127.0.0.1', 8642), timeout=10)
        upstream.sendall(first)
        client.settimeout(30); upstream.settimeout(30)
        with selectors.DefaultSelector() as selector:
            selector.register(client, selectors.EVENT_READ, upstream)
            selector.register(upstream, selectors.EVENT_READ, client)
            while not stopping.is_set():
                for key, _ in selector.select(timeout=1):
                    data = key.fileobj.recv(65536)
                    if not data: return
                    key.data.sendall(data)
    except (OSError, TimeoutError): pass
    finally:
        client.close()
        if upstream: upstream.close()

def stop(*_):
    stopping.set()
    if child and child.poll() is None: child.terminate()

if __name__ == '__main__':
    # The token file is generated for this session only; it is not a provider key.
    token = open('/opt/data/dashboard-session.token').read().strip()
    if len(token) < 32: raise ValueError('Missing session authentication')
    session_token = token
    env = {**os.environ, 'HERMES_DASHBOARD_SESSION_TOKEN': token, 'HERMES_DESKTOP': '1'}
    child = subprocess.Popen(['/opt/hermes/bin/hermes', 'serve', '--host', '127.0.0.1', '--port', '8642', '--isolated'], env=env)
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    listener = socket.socket(); listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(('0.0.0.0', 8864)); listener.listen(32); listener.settimeout(1)
    workers = threading.BoundedSemaphore(32)
    try:
        while not stopping.is_set() and child.poll() is None:
            try: client, _ = listener.accept()
            except TimeoutError: continue
            if not workers.acquire(blocking=False): client.close(); continue
            def work(connection):
                try: forward(connection)
                finally: workers.release()
            threading.Thread(target=work, args=(client,), daemon=True).start()
    finally:
        listener.close(); stop()
        try: child.wait(timeout=10)
        except subprocess.TimeoutExpired: child.kill(); child.wait()
