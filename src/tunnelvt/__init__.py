"""tunnelvt — Simple tunneling. No login.

Expose local apps at ``domain/<device>/<app>`` through a tunnelvt server.
"""

from .client import TunnelVT

__version__ = "0.1.0"
__all__ = ["TunnelVT"]
