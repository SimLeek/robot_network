import os

# Generate 32 random bytes
random_data = os.urandom(32)

# Write to a file
with open("psk.key", "wb") as f:
    f.write(random_data)

print(f"Successfully wrote {len(random_data)} bytes to psk.key")