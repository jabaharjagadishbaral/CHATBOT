import hmac
import logging
import json
import time

logger = logging.getLogger('neighborhood_helpboard')

ALLOWED_TYPES = {'message', 'alert', 'offer', 'info', 'general'}
MAX_USERNAME_LEN = 30
MAX_TYPE_LEN = 20
MAX_MESSAGE_LEN = 1000
MAX_FILTER_LEN = 20


def _format_timestamp(timestamp):
    return time.ctime(timestamp)


def _safe_text(value):
    return str(value).strip()


def _validate_text(value, max_length):
    value = _safe_text(value)
    if not value or len(value) > max_length:
        return None
    return value


def process_command(parts, username, conn, addr, db, admin_token):
    cmd = parts[0].upper()
    if cmd == 'POST':
        if len(parts) < 3:
            conn.sendall(b'Usage: POST <type> <message>\n')
            return True

        type_ = _validate_text(parts[1], MAX_TYPE_LEN)
        if not type_ or type_.lower() not in ALLOWED_TYPES:
            conn.sendall(b'Invalid message type. Allowed: message, alert, offer, info, general.\n')
            return True

        message = _validate_text(' '.join(parts[2:]), MAX_MESSAGE_LEN)
        if message is None:
            conn.sendall(b'Invalid or empty message. Maximum length is 1000 characters.\n')
            return True

        username = _validate_text(username, MAX_USERNAME_LEN) or 'anonymous'
        db.add_post(username, type_.lower(), message)
        conn.sendall(b'~~~Post added~~~\n')
        logger.info('New post by %s: %s - %s', username, type_, message)
        return True
    elif cmd == 'LIST':
        type_filter = None
        if len(parts) > 1:
            filter_value = _validate_text(parts[1], MAX_FILTER_LEN)
            if filter_value and filter_value.lower() in ALLOWED_TYPES:
                type_filter = filter_value.lower()
        recent = db.list_posts(type_filter=type_filter, limit=10)
        if not recent:
            response = '~~~No posts found~~~\n'
        else:
            response = '\n'.join(
                f"{p['id']}: {p['type']} by {p['username']} at {_format_timestamp(p['timestamp'])}"
                for p in recent
            ) + '\n'
        conn.sendall(response.encode('utf-8') + b'\n')
        logger.info('LIST request by %s-%s', username, addr)
        return True
    elif cmd == 'LISTJSON':
        type_filter = None
        if len(parts) > 1:
            filter_value = _validate_text(parts[1], MAX_FILTER_LEN)
            if filter_value and filter_value.lower() in ALLOWED_TYPES:
                type_filter = filter_value.lower()
        recent = db.list_posts(type_filter=type_filter, limit=10)
        try:
            conn.sendall(json.dumps(recent).encode('utf-8'))
        except Exception:
            conn.sendall(b'[]')
        logger.info('LISTJSON request by %s-%s', username, addr)
        return True
    elif cmd == 'GET':
        if len(parts) < 2:
            conn.sendall(b'Usage: GET <id>\n')
            logger.warning('GET command missing ID from %s', addr)
            return True
        try:
            id_ = int(parts[1])
            if id_ <= 0:
                raise ValueError
            post = db.get_post(id_)
            if post:
                response = (
                    '=' * 50 + '\n'
                    + f"ID: {post['id']}\nUsername: {post['username']}\nType: {post['type']}\n"
                    + f"Message: {post['message']}\nTimestamp: {_format_timestamp(post['timestamp'])}\n"
                    + '\n' + '=' * 50
                )
                logger.info('Post %d retrieved successfully for %s', id_, addr)
            else:
                response = '~~~Post not found~~~\n'
                logger.warning('Post %d not found for %s', id_, addr)
            conn.sendall(response.encode('utf-8'))
        except ValueError:
            conn.sendall(b'Invalid ID.\n')
            logger.error('Invalid ID format in GET command from %s: %s', addr, parts[1])
        return True
    elif cmd == "EXIT":
        conn.send(b"~~~Goodbye!~~~\n")
        logger.info("EXIT command received from %s", addr)
        return False
    elif cmd == 'SHUTDOWN':
        if not admin_token or len(parts) < 2 or not hmac.compare_digest(parts[1], admin_token):
            conn.sendall(b'Invalid token.\n')
            logger.warning('Invalid SHUTDOWN token attempt from %s', addr)
            return True
        conn.sendall(b'~~~Server shutting down~~~\n')
        logger.warning('Shutdown command received from %s', addr)
        return 'shutdown'
    elif cmd == 'HELP':
        help_text = """Available commands:
POST <type> <message> - Post a new message
LIST [type] - List recent posts (optional filter by type)
GET <id> - Get full post by ID
EXIT - Disconnect from server
SHUTDOWN <token> - Shutdown server (admin only,requires token)
"""
        conn.send(help_text.encode())
        logger.info('HELP command received from %s', addr)
        return True
    else:
        conn.send(b"Unknown command. Type HELP for help.\n")
        logger.warning('Unknown command received from %s', addr)
        return True