"""Tailscale management on the Slate (status / up / down / config).

We drive the `tailscale` CLI binary on the Slate via SSH rather than the
Go library: the binary already exists in the GL.iNet firmware
(`gl-sdk4-tailscale`), it's well-tested, and shelling out keeps our code
stable across Tailscale version bumps.
"""
