import os
import socket
import threading
import logging

from database import Database
import routes

SHUTDOWN_EVENT = threading.Event()
MAX_USERNAME_LEN = 30

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('neighborhood_helpboard')


def handle_client(conn, addr, db, admin_token):
    username = None
    try:
        conn.sendall(b"Welcome to Neighborhood Helpboard!\nEnter your username: ")
        username = conn.recv(4096).decode('utf-8', errors='replace').strip()
        username = username[:MAX_USERNAME_LEN] or 'anonymous'
        logger.info('Client %s connected as %s', addr, username)
        conn.sendall(b"Connected. Type HELP for commands.\n")
        while not SHUTDOWN_EVENT.is_set():
            data = conn.recv(4096).decode('utf-8', errors='replace').strip()
            if not data:
                break
            parts = data.split()
            if not parts:
                continue
            try:
                cont = routes.process_command(parts, username, conn, addr, db, admin_token)
                if cont == 'shutdown':
                    SHUTDOWN_EVENT.set()
                    break
                if cont is False:
                    break
            except Exception as e:
                logger.exception('Error processing command from %s: %s', addr, e)
                break
    except Exception as e:
        logger.error('Error with client %s: %s', addr, e)
    finally:
        try:
            conn.close()
        except Exception:
            pass
        logger.info('Client %s disconnected', addr)

def main():
    server_IP = os.getenv('SERVER_IP', '0.0.0.0')
    server_port = int(os.getenv('SERVER_PORT', os.getenv('PORT', '7000')))
    admin_token = os.getenv('ADMIN_TOKEN', '')

    if not admin_token:
        logger.warning('ADMIN_TOKEN is not set. SHUTDOWN command is disabled.')

    db = Database()
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    server.bind((server_IP, server_port))
    server.listen(5)
    server.settimeout(1.0)
    logger.info('Server listening on %s:%s', server_IP, server_port)
    try:
        while not SHUTDOWN_EVENT.is_set():
            try:
                conn, addr = server.accept()
            except socket.timeout:
                continue
            threading.Thread(target=handle_client, args=(conn, addr, db, admin_token), daemon=True).start()
    except KeyboardInterrupt:
        logger.info('Server shutdown requested via KeyboardInterrupt')
    finally:
        server.close()
        db.close()
        logger.info('Server cleanly stopped')

if __name__ == "__main__":
    main()