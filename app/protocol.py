import json
import struct

# Protocol message types
MSG_HELLO = "HELLO"
MSG_HELLO_ACK = "HELLO_ACK"
MSG_PING = "PING"
MSG_PONG = "PONG"
MSG_MOUSE_MOVE = "MM"
MSG_MOUSE_BUTTON = "MB"
MSG_MOUSE_WHEEL = "MW"
MSG_KEY_EVENT = "KB"
MSG_CLIPBOARD = "CLIP"
MSG_SWITCH = "SWITCH"
MSG_RETURN = "RETURN"
MSG_DISCONNECT = "BYE"
MSG_FILE_OFFER = "FILE_OFFER"
MSG_FILE_ACK = "FILE_ACK"
MSG_FILE_DONE = "FILE_DONE"

HEADER_STRUCT = struct.Struct("!I")  # 4-byte big-endian unsigned int for payload length

def encode_message(msg_dict: dict) -> bytes:
    """Encodes a message dictionary into a length-prefixed JSON byte stream."""
    data = json.dumps(msg_dict, separators=(',', ':')).encode('utf-8')
    header = HEADER_STRUCT.pack(len(data))
    return header + data

def decode_stream(buffer: bytearray):
    """
    Extracts complete messages from a buffer.
    Yields parsed message dicts and updates buffer in-place.
    """
    messages = []
    while len(buffer) >= 4:
        msg_len = HEADER_STRUCT.unpack_from(buffer, 0)[0]
        if len(buffer) < 4 + msg_len:
            break
        # Extract message payload
        raw_msg = buffer[4 : 4 + msg_len]
        del buffer[: 4 + msg_len]
        try:
            msg = json.loads(raw_msg.decode('utf-8'))
            messages.append(msg)
        except Exception as e:
            print(f"[Protocol] Error parsing message: {e}")
    return messages
