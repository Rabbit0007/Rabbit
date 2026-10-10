#!/usr/bin/env python3
"""Simple TCP proxy: forwards connections from local port to a remote host:port.

Usage: python tcp_proxy.py [--listen-port 18443] [--remote-host api.deepseek.com] [--remote-port 443]
"""
import asyncio
import argparse
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("tcp_proxy")


async def pipe(src, dst, name):
    try:
        while True:
            data = await src.read(65536)
            if not data:
                break
            dst.write(data)
            await dst.drain()
    except Exception:
        pass
    finally:
        dst.close()
        LOG.debug("pipe %s closed", name)


async def handle(reader, writer, remote_host, remote_port):
    peer = writer.get_extra_info("peername")
    LOG.info("connection from %s", peer)
    try:
        r_reader, r_writer = await asyncio.wait_for(
            asyncio.open_connection(remote_host, remote_port), timeout=10.0
        )
    except Exception as e:
        LOG.error("failed to connect to %s:%s: %s", remote_host, remote_port, e)
        writer.close()
        return

    await asyncio.gather(
        pipe(reader, r_writer, f"{peer} -> remote"),
        pipe(r_reader, writer, f"remote -> {peer}"),
    )
    LOG.info("connection closed from %s", peer)


async def main(args):
    server = await asyncio.start_server(
        lambda r, w: handle(r, w, args.remote_host, args.remote_port),
        host="0.0.0.0",
        port=args.listen_port,
    )
    LOG.info(
        "listening on 0.0.0.0:%s, forwarding to %s:%s",
        args.listen_port,
        args.remote_host,
        args.remote_port,
    )
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--listen-port", type=int, default=18443)
    p.add_argument("--remote-host", default="api.deepseek.com")
    p.add_argument("--remote-port", type=int, default=443)
    args = p.parse_args()
    asyncio.run(main(args))
