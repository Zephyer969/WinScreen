"""Translate VT keyboard text to ConPTY's negotiated Win32 input records.

Protocol: microsoft/terminal doc/specs/#4999 - Improved keyboard handling in Conpty.md.
Without virtual-key metadata, a late Enter can appear on screen but not execute.
"""
import re

RECORD = re.compile(r'\x1b\[[0-9;]*_')
CSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
KEYS = {'\x1b[A': 38, '\x1b[B': 40, '\x1b[C': 39, '\x1b[D': 37,
        '\x1b[H': 36, '\x1b[F': 35, '\x1b[1~': 36, '\x1b[2~': 45,
        '\x1b[3~': 46, '\x1b[4~': 35, '\x1b[5~': 33, '\x1b[6~': 34,
        '\x1b[7~': 36, '\x1b[8~': 35}


def record(vk, char=0, control=0):
    # Include key-up. Down-only repeated virtual keys can be coalesced by
    # Windows/PSReadLine and produce an extra repeated character.
    return ('\x1b[%s;0;%s;1;%s;1_' % (vk, char, control)
            + '\x1b[%s;0;%s;0;%s;1_' % (vk, char, control))


def win32_input(text):
    output = []
    pos = 0
    while pos < len(text):
        existing = RECORD.match(text, pos)
        if existing:
            output.append(existing[0])
            pos = existing.end()
            continue
        special = next((key for key in KEYS if text.startswith(key, pos)), None)
        if special:
            output.append(record(KEYS[special]))
            pos += len(special)
            continue
        # Focus reports (CSI I/O), cursor reports and bracketed-paste markers
        # are terminal protocol, not literal keyboard characters.
        protocol = CSI.match(text, pos)
        if protocol:
            output.append(protocol[0])
            pos = protocol.end()
            continue
        char = text[pos]
        pos += 1
        if char in '\r\n':
            output.append(record(13, 13))
        elif char == '\x03':
            # ConPTY's legacy ETX path performs processed Ctrl+C delivery.
            output.append(char)
        elif char in '\x08\x7f':
            output.append(record(8, 8))
        elif char == '\t':
            output.append(record(9, 9))
        elif char == '\x1b':
            output.append(record(27, 27))
        elif 0 < ord(char) < 27:
            output.append(record(ord(char) + 64, ord(char), 8))
        else:
            vk = ord(char.upper()) if char.isascii() and char.isalnum() else 0
            units = char.encode('utf-16-le', errors='surrogatepass')
            for index in range(0, len(units), 2):
                output.append(record(vk, int.from_bytes(units[index:index+2], 'little')))
    return ''.join(output)
