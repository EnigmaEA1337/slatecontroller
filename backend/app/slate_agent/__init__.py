"""Slate local-agent installer + sync.

The "agent" is a set of POSIX shell scripts that live on the Slate at
`/etc/slate-controller/` + `/usr/local/bin/slate-ctrl`. Once deployed,
the Slate can apply profiles locally — no controller involvement
needed — which is what makes the physical button + boot-time reapply
work even when the controller is offline.

Modules:
  deploy : push the shell scripts (slate-ctrl + handlers) onto the Slate
  sync   : serialize profile Pydantic models to JSON and push them
  invoke : call `slate-ctrl apply <name>` over SSH from the controller

The shell scripts themselves live in app/slate_agent/scripts/. They're
read at runtime and pushed via SlateSSH.put_bytes — they don't ship
as separate files on the Slate filesystem image; we control the version
end-to-end.
"""
