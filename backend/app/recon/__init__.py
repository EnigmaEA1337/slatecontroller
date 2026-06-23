"""Network reconnaissance from the Slate's perspective.

The "Reconnaissance WAN" security feature drives an active discovery
sweep on every L3 interface the operator selects (typically the WAN
uplink + every active bridge). Four phases run in order :

  1. **ARP cache pickup** — read ``ip neigh`` for every interface,
     gives us the cheap wins (anything that talked to us recently).
  2. **Ping sweep** — ICMP echo across the interface's /24 (or /N
     when smaller) to wake up silent hosts. Re-read ``ip neigh``
     after the sweep so newly-resolved MACs land in the host list.
  3. **TCP probe** — for every discovered IP, connect to a small
     set of well-known ports (22, 80, 443, 445, 3389, 8080, …).
  4. **Banner grab** — for every open port, do a tiny non-intrusive
     read (HTTP HEAD, SSH greeting, …) and store the first 256 chars.

Tools we can rely on (busybox + GL.iNet stock) :
  - ``ip neigh show dev <iface>``
  - ``ip -4 addr show dev <iface>`` / ``ip route show``
  - ``ping -c1 -W1 -I <iface> <ip>``
  - ``nc -z -w1 <ip> <port>`` (or python sockets via the runner)

This package builds the orchestration in pure Python on the
controller side : every shell snippet is issued over the existing
:class:`app.slate.ssh.SlateSSH` pipe, results are parsed in Python
and persisted via :mod:`.store`.
"""
