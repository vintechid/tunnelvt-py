# tunnelvt — Python

Simple tunneling client in Python. No login. Expose local apps at `domain/<device>/<app>`.

## Install

```bash
pip install tunnelvt
```

Requires Python ≥ 3.9.

## Usage

```bash
tunnelvt -s https://tunnel.example.com -a myapp -p 3000
```

Output:
```
[tunnelvt] connected — a1b2c3d4e5f6g7h8/myapp -> localhost:3000
```

Your app is now at:

```
https://tunnel.example.com/a1b2c3d4e5f6g7h8/myapp/
```

### Custom device ID

```bash
tunnelvt -s https://tunnel.example.com -d my-laptop -a api -p 8080
# → https://tunnel.example.com/my-laptop/api/
```

### Library usage

```python
from tunnelvt import TunnelVT

client = TunnelVT(
    server_url="https://tunnel.example.com",
    app="myapp",
    port=3000,
)
client.connect()  # blocks
```

## License

MIT
