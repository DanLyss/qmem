import time, os
time.sleep(1)                                   # let tracing start
print("phase 1: allocate 300 MB", flush=True)
big = [bytearray(1 << 20) for _ in range(300)]  # 300 x 1 MB, written (zeroed)
time.sleep(1)
print("phase 2: hammer 30 MB, read 100 MB", flush=True)
end = time.time() + 4
while time.time() < end:
    for b in big[:30]:                          # write-hot
        b[::4096] = b'\x01' * len(b[::4096])
    s = 0
    for b in big[100:200]:                      # read-only
        s += b[0] + b[2048]
print("phase 3: free 200 MB, read a file", flush=True)
del big[100:]
data = open('/usr/bin/python3', 'rb').read()   # page cache
time.sleep(2)
print("done", flush=True)
