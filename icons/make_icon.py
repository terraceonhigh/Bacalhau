#!/usr/bin/env python3
"""Generate a placeholder 1024x1024 PNG icon for Bacalhau."""
import struct, zlib

width = height = 1024
# Solid dark background with gold center block — built row by row for speed
bg = bytes([26, 26, 26, 255]) * width
gold = bytes([181, 155, 91, 255])
dark = bytes([26, 26, 26, 255])

rows = []
for y in range(height):
    row = b'\x00'  # PNG filter: none
    if 392 <= y <= 632:
        # Row with gold center block (412..612)
        row += dark * 412 + gold * 200 + dark * (width - 612)
    else:
        row += bg
    rows.append(row)

raw = b''.join(rows)

def chunk(ctype, data):
    c = ctype + data
    return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)

ihdr = struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)
idat = zlib.compress(raw, 9)

with open('icon.png', 'wb') as f:
    f.write(b'\x89PNG\r\n\x1a\n')
    f.write(chunk(b'IHDR', ihdr))
    f.write(chunk(b'IDAT', idat))
    f.write(chunk(b'IEND', b''))

print("Created icon.png (1024x1024)")
