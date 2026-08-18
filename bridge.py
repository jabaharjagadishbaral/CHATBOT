import os
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import json
from urllib.parse import urlparse, parse_qs

# Configuration
# HTTP port: prefer explicit HTTP_PORT, otherwise use platform PORT (e.g. Render sets PORT)
HTTP_PORT = int(os.getenv('HTTP_PORT', os.getenv('PORT', '8000')))
HTTP_BIND = os.getenv('HTTP_BIND', '0.0.0.0')
TCP_SERVER_IP = os.getenv('TCP_SERVER_IP', '127.0.0.1')
TCP_SERVER_PORT = int(os.getenv('SERVER_PORT', os.getenv('TCP_PORT', '7000')))
ALLOWED_ORIGINS = [origin.strip() for origin in os.getenv('ALLOWED_ORIGINS', 'http://localhost:8000').split(',') if origin.strip()]
MAX_USERNAME_LEN = 30
MAX_TYPE_LEN = 20
MAX_MESSAGE_LEN = 1000
MAX_FILTER_LEN = 20
MAX_LIST_LIMIT = 50
ALLOWED_TYPES = {'message', 'alert', 'offer', 'info', 'general'}

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('neighborhood_helpboard')

class AdvancedBridgeHandler(BaseHTTPRequestHandler):
    server_version = ''
    sys_version = ''

    def _set_headers(self, status_code=200, content_type='application/json'):
        self.send_response(status_code)
        self.send_header('Content-Type', content_type)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        self.send_header('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')

        origin = self.headers.get('Origin')
        if origin in ALLOWED_ORIGINS:
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Vary', 'Origin')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_OPTIONS(self):
        # Handle CORS pre-flight browser requests
        self._set_headers(200)

    # FEATURE 1: Fetching & Filtering Messages
    def do_GET(self):
        parsed_url = urlparse(self.path)
        
        if parsed_url.path == '/health':
            self._set_headers(200)
            self.wfile.write(json.dumps({"status": "ok"}).encode('utf-8'))
            return

        if parsed_url.path in ('/', '/index.html'):
            try:
                with open('index.html', 'rb') as f:
                    content = f.read()
                self._set_headers(200, content_type='text/html; charset=utf-8')
                self.wfile.write(content)
            except FileNotFoundError:
                self._set_headers(500, content_type='application/json')
                self.wfile.write(json.dumps({"error": "index.html not found"}).encode('utf-8'))
            return

        if parsed_url.path.startswith('/assets/'):
            assets_dir = os.path.realpath('assets')
            requested_name = os.path.basename(parsed_url.path)  # strips any path components
            candidate = os.path.realpath(os.path.join(assets_dir, requested_name))

            # Ensure the resolved path is still inside the assets directory
            if not requested_name or os.path.commonpath([assets_dir, candidate]) != assets_dir:
                self._set_headers(404, content_type='application/json')
                self.wfile.write(json.dumps({"error": "Asset not found"}).encode('utf-8'))
                return

            try:
                with open(candidate, 'rb') as f:
                    content = f.read()
                content_type = 'image/png' if candidate.endswith('.png') else 'application/octet-stream'
                self._set_headers(200, content_type=content_type)
                self.wfile.write(content)
            except FileNotFoundError:
                self._set_headers(404, content_type='application/json')
                self.wfile.write(json.dumps({"error": "Asset not found"}).encode('utf-8'))
            return

        if parsed_url.path == '/messages':
            query_params = parse_qs(parsed_url.query)
            type_filter = query_params.get('type_filter', [''])[0].strip()[:MAX_TYPE_LEN]
            if type_filter and type_filter.lower() not in ALLOWED_TYPES:
                type_filter = ''
            limit = 10
            try:
                requested_limit = int(query_params.get('limit', ['10'])[0])
                if 1 <= requested_limit <= MAX_LIST_LIMIT:
                    limit = requested_limit
            except ValueError:
                limit = 10

            tcp_command = 'LISTJSON' if not type_filter else f'LISTJSON {type_filter.lower()}'
            tcp_response = self.talk_to_tcp_server(tcp_command)
            try:
                parsed = json.loads(tcp_response)
                if isinstance(parsed, list):
                    parsed = parsed[:limit]
                else:
                    parsed = []
            except Exception:
                parsed = []
            self._set_headers(200)
            self.wfile.write(json.dumps(parsed).encode('utf-8'))
            return

        self._set_headers(404)
        self.wfile.write(json.dumps({"error": "Endpoint not found"}).encode('utf-8'))

    # FEATURE 2: Submitting a New Categorized Message
    def do_POST(self):
        parsed_url = urlparse(self.path)
        
        if parsed_url.path == '/messages':
            content_type = self.headers.get('Content-Type', '')
            if not content_type.startswith('application/json'):
                self._set_headers(415)
                self.wfile.write(json.dumps({'error': 'Content-Type must be application/json'}).encode('utf-8'))
                return

            content_length = int(self.headers.get('Content-Length', 0))
            if content_length <= 0:
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': 'Missing request body'}).encode('utf-8'))
                return

            body = self.rfile.read(content_length)
            try:
                web_data = json.loads(body.decode('utf-8'))
            except Exception:
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': 'Invalid JSON payload'}).encode('utf-8'))
                return

            username = str(web_data.get('username', 'bridge_http')).strip()[:MAX_USERNAME_LEN] or 'bridge_http'
            type_ = str(web_data.get('type', 'general')).strip()[:MAX_TYPE_LEN] or 'general'
            if type_ not in ALLOWED_TYPES:
                type_ = 'general'
            message = str(web_data.get('message', '')).strip()[:MAX_MESSAGE_LEN]
            message = message.replace('\n', ' ').replace('\r', ' ')

            if not message:
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': 'Message is required'}).encode('utf-8'))
                return

            tcp_command = f'POST {type_} {message}'
            tcp_response = self.talk_to_tcp_server(tcp_command, handshake_username=username)
            self._set_headers(200)
            try:
                resp_text = tcp_response.strip()
                self.wfile.write(json.dumps({'detail': resp_text}).encode('utf-8'))
            except Exception:
                self.wfile.write(json.dumps({'detail': 'ok'}).encode('utf-8'))
            return
        else:
            self._set_headers(404)
            self.wfile.write(json.dumps({'error': 'Endpoint not found'}).encode('utf-8'))

    # TCP Core Translation Engine
    def talk_to_tcp_server(self, payload, handshake_username='bridge_http'):
        try:
            with socket.create_connection((TCP_SERVER_IP, TCP_SERVER_PORT), timeout=2.0) as tcp_socket:
                tcp_socket.settimeout(2.0)
                try:
                    tcp_socket.recv(4096).decode('utf-8', errors='replace')
                except socket.timeout:
                    pass

                tcp_socket.sendall((handshake_username + '\n').encode('utf-8'))
                try:
                    tcp_socket.recv(4096).decode('utf-8', errors='replace')
                except socket.timeout:
                    pass

                tcp_socket.sendall((payload + '\n').encode('utf-8'))
                response_parts = []
                while True:
                    try:
                        chunk = tcp_socket.recv(4096)
                        if not chunk:
                            break
                        response_parts.append(chunk.decode('utf-8', errors='replace'))
                        if len(chunk) < 4096:
                            break
                    except socket.timeout:
                        break
                return ''.join(response_parts).strip()
        except Exception as e:
            logger.error('Could not communicate with TCP server: %s', e)
            if str(payload).upper().startswith('LIST'):
                return json.dumps([])
            return json.dumps({'detail': 'Database/TCP Server connection offline.'})

def run():
    logger.info('Upgraded HTTP Gateway running at http://%s:%s', TCP_SERVER_IP, HTTP_PORT)
    server = ThreadingHTTPServer((HTTP_BIND, HTTP_PORT), AdvancedBridgeHandler)
    server.daemon_threads = True
    server.allow_reuse_address = True
    server.serve_forever()

if __name__ == '__main__':
    run()